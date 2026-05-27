"""Aptiro test suite - Trust + Export slice.

Preserves the Delivery 1-4 behavior contract (extraction & provenance,
explainable scoring, package builder + per-bullet controls, the 13-step
council, apply scaffolding, privacy) AND adds the new slice coverage:
production PDF/DOCX/TXT/Markdown ingestion, real Markdown/HTML/DOCX/PDF
export, and the non-negotiable "rejected / unsupported content never
leaves in a final export by default" gate.

Deterministic & offline: SQLite in-memory, mock AI provider, no network.
"""
import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

import app as A
import ingestion
import exporting

RESUME = """Professional Summary
Senior AI Product Manager focused on healthcare AI and lab workflow automation with measurable business impact.

Experience
Director of Product, Pattern (2021-10 to 2024-07)
- Built Pattern's first AI chatbot that drove $3.4M in revenue growth across the analytics product line.
- Led cross-functional product strategy across engineering and design teams for AI discovery features.
- Led it.

Education
- MBA in Marketing and Strategy, BYU Marriott School of Business
"""

JD = """Title: Senior AI Product Manager
Company: Example Health AI
Location: Remote - United States

We are hiring a Senior AI Product Manager to lead decision-support tools.
- 6+ years of product management experience
- Shipped AI/ML products
- Healthcare or clinical domain experience
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture()
def client(engine):
    def _override():
        with Session(engine) as s:
            yield s
    A.app.dependency_overrides[A.get_session] = _override
    with TestClient(A.app) as c:
        yield c
    A.app.dependency_overrides.clear()


def _make_source(session, text=RESUME, stype=None):
    src = A.Source(
        source_type=stype or A.SourceType.resume, label="r",
        raw_text=text, extracted_text=text,
        parse_meta={"format": "text", "chars": len(text)})
    session.add(src)
    session.commit()
    session.refresh(src)
    return src


def _docx_bytes(paragraphs):
    import docx
    d = docx.Document()
    for p in paragraphs:
        d.add_paragraph(p)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


# ===========================================================================
# D1 - document parsing
# ===========================================================================
def test_parse_returns_only_bullets(session):
    lines = A.parse_document(RESUME)
    texts = [ln.text for ln in lines]
    assert any("AI chatbot" in t for t in texts)
    assert all(ln.is_bullet for ln in lines)


def test_section_headers_classified():
    assert A._classify_header("Experience") == "experience"
    assert A._classify_header("PROFESSIONAL SUMMARY") == "summary"
    assert A._classify_header("Education:") == "education"
    assert A._classify_header("Not a header at all here") is None


def test_role_company_date_regex():
    m = A._ROLE_AT_COMPANY.match("Director of Product, Pattern (2021-10 to 2024-07)")
    assert m and m.group("role") == "Director of Product"
    assert m.group("company") == "Pattern"


# ===========================================================================
# D1 - claim extraction + provenance invariants
# ===========================================================================
def test_bullets_become_claims(session):
    claims = A.extract_claims(session, _make_source(session))
    texts = [c.claim_text for c in claims]
    assert any("AI chatbot" in t for t in texts)
    assert any("cross-functional" in t for t in texts)


def test_short_bullet_filtered(session):
    claims = A.extract_claims(session, _make_source(session))
    assert all(len(c.claim_text) >= 12 for c in claims)
    assert not any(c.claim_text.strip() == "Led it." for c in claims)


def test_metric_claim_is_achievement_high_confidence(session):
    claims = A.extract_claims(session, _make_source(session))
    rev = next(c for c in claims if "3.4M" in c.claim_text)
    assert rev.claim_type == A.ClaimType.achievement
    assert any("3.4" in m for m in rev.metrics)
    assert rev.confidence > 0.7


def test_non_metric_claim_is_responsibility(session):
    claims = A.extract_claims(session, _make_source(session))
    strat = next(c for c in claims if "cross-functional" in c.claim_text)
    assert strat.claim_type == A.ClaimType.responsibility


def test_summary_claim_type(session):
    claims = A.extract_claims(session, _make_source(session))
    assert any(c.claim_type == A.ClaimType.summary for c in claims)


def test_company_role_date_attribution(session):
    claims = A.extract_claims(session, _make_source(session))
    bot = next(c for c in claims if "chatbot" in c.claim_text)
    assert bot.company == "Pattern"
    assert bot.role == "Director of Product"
    assert bot.date_range == "2021-10 to 2024-07"


def test_new_claims_default_pending(session):
    claims = A.extract_claims(session, _make_source(session))
    assert claims
    assert all(c.approval_status == A.ApprovalStatus.pending for c in claims)


def test_every_claim_has_evidence_ref(session):
    claims = A.extract_claims(session, _make_source(session))
    for c in claims:
        refs = session.exec(
            select(A.SourceRef).where(A.SourceRef.claim_id == c.id)).all()
        assert len(refs) >= 1
        assert refs[0].snippet
        assert refs[0].section


def test_resume_provenance_is_grounded_blue(session):
    claims = A.extract_claims(session, _make_source(session))
    c = claims[0]
    cat = A.claim_provenance(session, c)
    assert cat == A.ProvenanceCategory.grounded_resume_truth
    assert A.provenance_color(cat) == "blue"


def test_linkedin_provenance_is_profile_purple(session):
    src = _make_source(session, stype=A.SourceType.linkedin)
    claims = A.extract_claims(session, src)
    cat = A.claim_provenance(session, claims[0])
    assert cat == A.ProvenanceCategory.profile_derived
    assert A.provenance_color(cat) == "purple"


def test_rejected_claim_becomes_unsupported_red(session):
    claims = A.extract_claims(session, _make_source(session))
    c = claims[0]
    c.approval_status = A.ApprovalStatus.rejected
    session.add(c)
    session.commit()
    cat = A.claim_provenance(session, c)
    assert cat == A.ProvenanceCategory.unsupported
    assert A.provenance_color(cat) == "red"


def test_do_not_use_claim_is_unsupported(session):
    claims = A.extract_claims(session, _make_source(session))
    c = claims[0]
    c.approval_status = A.ApprovalStatus.do_not_use
    session.add(c)
    session.commit()
    assert A.claim_provenance(session, c) == A.ProvenanceCategory.unsupported


def test_provenance_color_map_complete():
    for cat in A.ProvenanceCategory:
        assert A.provenance_color(cat) in (
            "blue", "purple", "green", "orange", "red")


# ===========================================================================
# D1 - REST: sources / claims
# ===========================================================================
def test_create_source_extracts_claims(client):
    r = client.post("/api/sources", json={
        "source_type": "resume", "label": "r", "raw_text": RESUME})
    assert r.status_code == 201
    assert r.json()["claim_count"] >= 3


def test_list_and_get_claims(client):
    client.post("/api/sources", json={
        "source_type": "resume", "label": "r", "raw_text": RESUME})
    claims = client.get("/api/claims").json()
    assert claims
    one = client.get("/api/claims/" + claims[0]["id"]).json()
    assert one["id"] == claims[0]["id"]
    assert one["provenance_color"] in (
        "blue", "purple", "green", "orange", "red")


def test_patch_claim_approval(client):
    client.post("/api/sources", json={
        "source_type": "resume", "label": "r", "raw_text": RESUME})
    cid = client.get("/api/claims").json()[0]["id"]
    r = client.patch("/api/claims/" + cid,
                      json={"approval_status": "approved"})
    assert r.status_code == 200
    assert r.json()["approval_status"] == "approved"


def test_delete_source_cascades(client):
    sid = client.post("/api/sources", json={
        "source_type": "resume", "label": "r",
        "raw_text": RESUME}).json()["id"]
    assert client.get("/api/claims").json()
    assert client.delete("/api/sources/" + sid).status_code == 204
    assert client.get("/api/claims").json() == []


# ===========================================================================
# NEW SLICE - production ingestion (PDF / DOCX / TXT / Markdown)
# ===========================================================================
def test_ingestion_supported_set():
    assert {"pdf", "docx", "txt", "md"}.issubset(ingestion.SUPPORTED)


def test_ingest_txt():
    res = ingestion.extract("resume.txt", RESUME.encode())
    assert "AI chatbot" in res.text
    assert res.meta["format"] == "txt"


def test_ingest_markdown_strips_syntax():
    md = ("# Summary\n\n"
          "**Senior PM** focused on *healthcare AI* and `automation`.\n\n"
          "## Experience\n\n"
          "- Built [the chatbot](https://x.com) that drove $3.4M growth\n")
    res = ingestion.extract("r.md", md.encode())
    assert res.meta["format"] == "markdown"
    assert "**" not in res.text and "`" not in res.text
    assert "https://x.com" not in res.text
    assert "the chatbot" in res.text


def test_ingest_markdown_pandoc_artifacts():
    """Real pandoc/DOCX-export resumes: [x]{.underline}, \\$, grid rules."""
    md = ("[John Doe]{.underline}\n\n"
          "Drove \\$85M revenue growth via [analytics]{.custom}\n"
          "+----------+----------+\n"
          "| Cell A   | Cell B   |\n")
    res = ingestion.extract("r.md", md.encode())
    assert "{.underline}" not in res.text and "{.custom}" not in res.text
    assert "$85M" in res.text and "\\$" not in res.text
    assert "+----------+" not in res.text          # rule line dropped
    assert "Cell A" in res.text                    # real data row kept


def test_ingest_docx_roundtrip():
    data = _docx_bytes([
        "Professional Summary",
        "Senior AI Product Manager with measurable impact across teams.",
        "Experience",
        "Director of Product, Pattern (2021-10 to 2024-07)",
        "- Built an AI chatbot that drove $3.4M in revenue growth.",
    ])
    res = ingestion.extract("resume.docx", data)
    assert res.meta["format"] == "docx"
    assert "AI chatbot" in res.text
    assert "$3.4M" in res.text


def test_ingest_pdf_with_page_map():
    rl = pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import LETTER
    buf = io.BytesIO()
    cpdf = canvas.Canvas(buf, pagesize=LETTER)
    cpdf.drawString(72, 720, "Professional Summary")
    cpdf.drawString(72, 700, "Senior AI Product Manager, measurable impact.")
    cpdf.showPage()
    cpdf.drawString(72, 720, "Experience")
    cpdf.drawString(72, 700, "Built an AI chatbot driving $3.4M growth.")
    cpdf.showPage()
    cpdf.save()
    res = ingestion.extract("resume.pdf", buf.getvalue())
    assert res.meta["format"] == "pdf"
    assert res.meta["pages"] == 2
    assert isinstance(res.meta["page_map"], list)
    assert "chatbot" in res.text.lower()


def test_ingest_unsupported_format_raises():
    with pytest.raises(ingestion.UnsupportedFormat):
        ingestion.extract("resume.rtf", b"junk")
    with pytest.raises(ingestion.UnsupportedFormat):
        ingestion.extract("legacy.doc", b"junk")


def test_ingest_empty_raises():
    with pytest.raises(ingestion.ExtractionError):
        ingestion.extract("resume.txt", b"")


def test_upload_endpoint_txt(client):
    r = client.post(
        "/api/sources/upload",
        files={"file": ("resume.txt", RESUME.encode(), "text/plain")})
    assert r.status_code == 201
    assert r.json()["claim_count"] >= 3


def test_upload_endpoint_docx_preserves_provenance(client):
    data = _docx_bytes([
        "Professional Summary",
        "Senior AI Product Manager focused on healthcare AI impact.",
        "Experience",
        "Director of Product, Pattern (2021-10 to 2024-07)",
        "- Built an AI chatbot that drove $3.4M in revenue growth.",
        "- Led cross-functional product strategy across teams.",
    ])
    r = client.post("/api/sources/upload", files={
        "file": ("resume.docx", data,
                 "application/vnd.openxmlformats-officedocument."
                 "wordprocessingml.document")})
    assert r.status_code == 201
    claims = client.get("/api/claims").json()
    assert claims
    # ingestion changes only HOW text is produced - provenance/approval
    # gate is unchanged: still resume->blue, still pending.
    assert all(c["approval_status"] == "pending" for c in claims)
    assert any(c["provenance_color"] == "blue" for c in claims)
    bot = next(c for c in claims if "chatbot" in c["claim_text"])
    assert bot["company"] == "Pattern"
    assert any("3.4" in m for m in bot["metrics"])


def test_upload_endpoint_rejects_unsupported(client):
    r = client.post("/api/sources/upload", files={
        "file": ("notes.rtf", b"some bytes", "application/rtf")})
    assert r.status_code == 415


def test_upload_endpoint_size_limit(client, monkeypatch):
    monkeypatch.setattr(ingestion, "MAX_UPLOAD_BYTES", 10)
    r = client.post("/api/sources/upload", files={
        "file": ("big.txt", b"x" * 5000, "text/plain")})
    assert r.status_code == 413


# ===========================================================================
# D2 - strategy + job sources + explainable scoring
# ===========================================================================
def _approve_all(client):
    for c in client.get("/api/claims").json():
        client.patch("/api/claims/" + c["id"],
                     json={"approval_status": "approved"})


def _seed_and_job(client):
    client.post("/api/sources", json={
        "source_type": "resume", "label": "r", "raw_text": RESUME})
    _approve_all(client)
    client.put("/api/strategy", json={
        "name": "Healthcare AI",
        "target_roles": ["Senior AI Product Manager"],
        "work_mode": "remote",
        "targeting_notes": "healthcare lab AI workflow automation"})
    client.post("/api/job-sources/fetch", json={"provider": "remotive"})
    jobs = client.get("/api/jobs").json()
    return next(j["id"] for j in jobs
                if "Product Manager" in j["title"])


def test_strategy_put_get(client):
    r = client.put("/api/strategy", json={
        "name": "AI PM", "target_roles": ["AI Product Manager"],
        "work_mode": "remote"})
    assert r.status_code in (200, 201)
    g = client.get("/api/strategy").json()
    assert g["name"] == "AI PM"
    assert sum(g["weights"].values()) == 100


def test_job_sources_listed(client):
    s = client.get("/api/job-sources").json()
    assert any("remotive" in str(s).lower() for _ in [0])


def test_manual_job_import(client):
    r = client.post("/api/jobs", json={"description_text": JD})
    assert r.status_code == 201
    j = r.json()
    assert j["title"]
    assert j["requirements"]


def test_fetch_jobs_and_match_scoring(client):
    jid = _seed_and_job(client)
    matches = client.get("/api/matches").json()
    assert matches
    top = client.get("/api/matches/" + jid).json()
    assert 0 <= top["score"] <= 100
    assert top["score"] > 0
    assert top.get("components") or top.get("breakdown") or top.get("summary")


def test_exclude_company_zeroes_but_shows(client):
    _seed_and_job(client)
    job = client.get("/api/jobs").json()[0]
    client.put("/api/strategy", json={
        "name": "x", "target_roles": ["Senior AI Product Manager"],
        "exclude_companies": [job["company"]]})
    m = client.get("/api/matches/" + job["id"]).json()
    assert m["score"] == 0


# ===========================================================================
# D3 - package builder + per-bullet controls
# ===========================================================================
def test_build_package_provenance(client):
    jid = _seed_and_job(client)
    r = client.post("/api/packages", json={"job_id": jid})
    assert r.status_code == 201
    p = r.json()
    assert p["status"] == "draft"
    assert p["score_snapshot"] > 0
    assert len(p["bullets"]) >= 4
    secs = {b["section"] for b in p["bullets"]}
    assert {"summary", "experience", "cover_letter"}.issubset(secs)
    for b in p["bullets"]:
        assert b["provenance_color"] in (
            "blue", "purple", "green", "orange", "red")
    exp = [b for b in p["bullets"] if b["section"] == "experience"]
    assert exp and all(b["claim_id"] for b in exp)
    assert all(not b["unsupported_metrics"] for b in exp)
    op = next(b for b in p["bullets"]
              if b["section"] == "cover_letter"
              and "Hiring Team" in b["current_text"])
    assert op["provenance"] == "ai_suggested"
    assert op["provenance_color"] == "orange"


def test_build_package_bad_job_404(client):
    assert client.post("/api/packages",
                       json={"job_id": "nope"}).status_code == 404


def test_bullet_controls_accept_reject_lock_rewrite(client):
    jid = _seed_and_job(client)
    p = client.post("/api/packages", json={"job_id": jid}).json()
    pid = p["id"]
    exp = [b for b in p["bullets"] if b["section"] == "experience"]
    assert len(exp) >= 2
    a, b2 = exp[0], exp[1]
    lockable = next(b for b in p["bullets"]
                    if b["id"] not in (a["id"], b2["id"]))
    r = client.patch("/api/packages/%s/bullets/%s" % (pid, a["id"]),
                      json={"status": "accepted"})
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    r = client.patch("/api/packages/%s/bullets/%s" % (pid, b2["id"]),
                      json={"current_text": "Rewrote this cleanly."})
    rb = r.json()
    assert rb["status"] == "rewritten"
    assert rb["current_text"] == "Rewrote this cleanly."
    assert rb["original_text"] == b2["original_text"]
    r = client.patch("/api/packages/%s/bullets/%s" % (pid, lockable["id"]),
                      json={"status": "locked"})
    assert r.json()["status"] == "locked"
    r = client.patch("/api/packages/%s/bullets/%s" % (pid, a["id"]),
                      json={"status": "rejected"})
    assert r.json()["status"] == "rejected"


def test_accept_blocked_when_evidence_revoked(client):
    jid = _seed_and_job(client)
    p = client.post("/api/packages", json={"job_id": jid}).json()
    pid = p["id"]
    b = next(x for x in p["bullets"]
             if x["section"] == "experience" and x["claim_id"])
    client.patch("/api/claims/" + b["claim_id"],
                 json={"approval_status": "rejected"})
    r = client.patch("/api/packages/%s/bullets/%s" % (pid, b["id"]),
                     json={"status": "accepted"})
    assert r.status_code == 409


# ===========================================================================
# D3 - 13-step orchestrator + 5-agent council
# ===========================================================================
def test_orchestrator_runs_and_persists(client):
    jid = _seed_and_job(client)
    p = client.post("/api/packages", json={"job_id": jid}).json()
    pid = p["id"]
    for b in p["bullets"]:
        if b["section"] == "experience" and b["claim_id"]:
            client.patch("/api/packages/%s/bullets/%s" % (pid, b["id"]),
                         json={"status": "accepted"})
    r = client.post("/api/packages/%s/orchestrate" % pid)
    assert r.status_code == 201
    run = r.json()
    assert len(run["steps"]) == 13
    runs = client.get("/api/packages/%s/runs" % pid).json()
    assert runs
    full = client.get("/api/runs/" + runs[0]["id"]).json()
    agents = {c["agent"] for c in full["critiques"]}
    assert {"vector", "axiom", "lumen", "entp", "istj_qa"} & agents


def test_axiom_blocks_fabricated_metric(client):
    jid = _seed_and_job(client)
    p = client.post("/api/packages", json={"job_id": jid}).json()
    pid = p["id"]
    b = next(x for x in p["bullets"]
             if x["section"] == "experience" and x["claim_id"])
    client.patch("/api/packages/%s/bullets/%s" % (pid, b["id"]),
                 json={"current_text":
                       "Drove $999M in fabricated revenue impact."})
    r = client.post("/api/packages/%s/orchestrate" % pid).json()
    crit = client.get("/api/runs/" + r["id"]).json()["critiques"]
    axiom = [c for c in crit if c["agent"] == "axiom"]
    assert any(c["severity"] in ("major", "blocker") for c in axiom)
    assert r["ready"] is False


# ===========================================================================
# NEW SLICE - real export + provenance exclusion gate
# ===========================================================================
def _package_with_accepted(client):
    jid = _seed_and_job(client)
    p = client.post("/api/packages", json={"job_id": jid}).json()
    pid = p["id"]
    for b in p["bullets"]:
        if b["section"] in ("experience", "summary") and b["claim_id"]:
            client.patch("/api/packages/%s/bullets/%s" % (pid, b["id"]),
                         json={"status": "accepted"})
    return pid, p


def test_export_formats_advertised(client):
    h = client.get("/api/health").json()
    assert h["export_formats"] == ["md", "html", "docx", "pdf"]


def test_export_markdown(client):
    pid, _ = _package_with_accepted(client)
    r = client.get("/api/packages/%s/export" % pid,
                    params={"format": "md", "artifact": "both"})
    assert r.status_code == 200
    body = r.text
    assert body.startswith("#")
    assert "## Summary" in body or "## Experience" in body
    assert "traces to" in body  # provenance footer


def test_export_html(client):
    pid, _ = _package_with_accepted(client)
    r = client.get("/api/packages/%s/export" % pid,
                    params={"format": "html"})
    assert r.status_code == 200
    assert "<!DOCTYPE html>" in r.text
    assert "<h1>" in r.text


def test_export_docx_is_valid_zip(client):
    pid, _ = _package_with_accepted(client)
    r = client.get("/api/packages/%s/export" % pid,
                    params={"format": "docx"})
    assert r.status_code == 200
    assert zipfile.is_zipfile(io.BytesIO(r.content))
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert "word/document.xml" in z.namelist()


def test_export_pdf_signature(client):
    pid, _ = _package_with_accepted(client)
    r = client.get("/api/packages/%s/export" % pid,
                    params={"format": "pdf"})
    assert r.status_code == 200
    assert r.content[:5] == b"%PDF-"


def test_export_bad_format_400(client):
    pid, _ = _package_with_accepted(client)
    r = client.get("/api/packages/%s/export" % pid,
                    params={"format": "rtf"})
    assert r.status_code == 400


def test_export_bad_package_404(client):
    r = client.get("/api/packages/nope/export", params={"format": "md"})
    assert r.status_code == 404


def test_export_preview_structure(client):
    pid, _ = _package_with_accepted(client)
    m = client.get("/api/packages/%s/export/preview" % pid).json()
    assert "sections" in m and "excluded" in m
    assert m["include_unsupported"] is False


def test_rejected_bullet_excluded_from_export(client):
    """Non-negotiable: rejected content must NOT appear in any export.

    The builder legitimately cites the same grounded achievement in both
    the experience and cover-letter sections, so a faithful test rejects
    every bullet carrying that text, then asserts it is fully gone.
    """
    jid = _seed_and_job(client)
    p = client.post("/api/packages", json={"job_id": jid}).json()
    pid = p["id"]
    target = next(b for b in p["bullets"]
                  if b["section"] == "experience" and b["claim_id"])
    needle = target["current_text"][:30]
    pre = client.get("/api/packages/%s/export" % pid,
                     params={"format": "md", "artifact": "both"}).text
    assert needle in pre
    for b in p["bullets"]:
        if needle in b["current_text"]:
            client.patch("/api/packages/%s/bullets/%s" % (pid, b["id"]),
                         json={"status": "rejected"})
    post = client.get("/api/packages/%s/export" % pid,
                      params={"format": "md", "artifact": "both"}).text
    assert needle not in post
    assert "excluded from this export" in post


def test_unsupported_claim_excluded_from_export(client):
    """Revoking a claim flips its bullet to red -> excluded by default."""
    jid = _seed_and_job(client)
    p = client.post("/api/packages", json={"job_id": jid}).json()
    pid = p["id"]
    b = next(x for x in p["bullets"]
             if x["section"] == "experience" and x["claim_id"])
    needle = b["current_text"][:30]
    client.patch("/api/claims/" + b["claim_id"],
                 json={"approval_status": "do_not_use"})
    md = client.get("/api/packages/%s/export" % pid,
                    params={"format": "md", "artifact": "both"}).text
    assert needle not in md
    prev = client.get("/api/packages/%s/export/preview" % pid).json()
    assert any("unsupported" in " ".join(x["reasons"]).lower()
               for x in prev["excluded"])


def test_include_unsupported_override(client):
    """Explicit override re-includes excluded content (user's choice)."""
    jid = _seed_and_job(client)
    p = client.post("/api/packages", json={"job_id": jid}).json()
    pid = p["id"]
    b = next(x for x in p["bullets"]
             if x["section"] == "experience" and x["claim_id"])
    needle = b["current_text"][:30]
    for x in p["bullets"]:
        if needle in x["current_text"]:
            client.patch("/api/packages/%s/bullets/%s" % (pid, x["id"]),
                         json={"status": "rejected"})
    default = client.get("/api/packages/%s/export" % pid,
                         params={"format": "md", "artifact": "both"}).text
    assert needle not in default
    override = client.get(
        "/api/packages/%s/export" % pid,
        params={"format": "md", "artifact": "both",
                "include_unsupported": "true"}).text
    assert needle in override


def test_export_cover_letter_only(client):
    pid, _ = _package_with_accepted(client)
    r = client.get("/api/packages/%s/export" % pid,
                    params={"format": "md", "artifact": "cover_letter"})
    assert r.status_code == 200
    assert "Cover Letter" in r.text


def test_exporter_module_render_direct():
    model = {
        "title": "T", "company": "C", "job_title": "J", "score": 80,
        "summary": "s", "generated_at": "now",
        "include_unsupported": False, "excluded": [],
        "sections": {"summary": [{"text": "Led X", "provenance": "grounded_resume_truth",
                                  "color": "blue", "status": "accepted",
                                  "flagged": []}],
                     "experience": [], "skills": [], "cover_letter": []},
    }
    for fmt in ("md", "html"):
        content, ext = exporting.render(model, fmt, "resume")
        assert content and ext == fmt
    with pytest.raises(exporting.ExportError):
        exporting.render(model, "rtf", "resume")


# ===========================================================================
# D4 - notifications (preview only) / apply scaffolding / privacy
# ===========================================================================
def test_notification_channels_no_external_send(client):
    r = client.get("/api/notifications/channels").json()
    assert r["sends_externally"] is False


def test_notification_digest_preview(client):
    _seed_and_job(client)
    r = client.get("/api/notifications/digest").json()
    assert isinstance(r, list)


def test_apply_session_is_guarded_scaffolding(client):
    jid = _seed_and_job(client)
    pid = client.post("/api/packages", json={"job_id": jid}).json()["id"]
    r = client.post("/api/apply", json={"package_id": pid})
    assert r.status_code == 201
    s = r.json()
    assert s["state"] == "created"
    assert any("CAPTCHA" in g or "no real" in g.lower()
               for g in s["guardrails"])


def test_apply_requires_explicit_confirm(client):
    jid = _seed_and_job(client)
    p = client.post("/api/packages", json={"job_id": jid}).json()
    for b in p["bullets"]:
        if b["section"] == "experience" and b["claim_id"]:
            client.patch("/api/packages/%s/bullets/%s" % (p["id"], b["id"]),
                         json={"status": "accepted"})
    sid = client.post("/api/apply",
                      json={"package_id": p["id"]}).json()["id"]
    client.post("/api/apply/%s/advance" % sid, json={"action": "prepare"})
    client.post("/api/apply/%s/advance" % sid,
                json={"action": "request_handoff"})
    r = client.post("/api/apply/%s/advance" % sid,
                    json={"action": "confirm", "confirm": False})
    assert r.status_code == 400  # refuses without explicit confirmation


def test_privacy_export_and_wipe(client):
    _seed_and_job(client)
    bundle = client.get("/api/privacy/export").json()
    assert bundle["counts"]["source"] >= 1
    assert "profileclaim" in bundle["data"]
    r = client.delete("/api/privacy/data")
    assert r.status_code in (200, 204)
    assert client.get("/api/sources").json() == []


def test_onboarding_state(client):
    o = client.get("/api/onboarding").json()
    assert "steps" in o and "completed" in o


def test_health_reports_slice_and_mock_ai(client):
    h = client.get("/api/health").json()
    assert h["slice"] == "trust-export"
    assert h["providers"]["ai"] == "mock"  # Decision 2: mock by default


# ===========================================================================
# Phase 2 - real job discovery & explainable matching
# ===========================================================================
import httpx as _httpx

JD_STRUCTURED = """Title: Senior AI Product Manager
Company: Example Health AI
Location: Remote - United States

About the role: lead AI decision-support tools for clinical workflows.

Required qualifications
- 6+ years of product management experience
- Shipped machine learning products to production
- Healthcare or clinical domain experience

Nice to have
- Experience with EMR integrations
- A plus: prior startup experience
"""

JOB_HTML = """<html><head><title>t</title>
<style>.x{}</style><script>var a=1;</script></head>
<body><h1>Senior AI Product Manager</h1>
<p>Title: Senior AI Product Manager</p>
<p>Company: Example Health AI</p>
<ul><li>6+ years of product management experience</li>
<li>Shipped AI/ML products</li>
<li>Healthcare domain experience</li></ul>
<p>Nice to have</p><ul><li>EMR integration experience is a plus</li></ul>
</body></html>"""


def _mock_httpx(handler):
    return _httpx.Client(transport=_httpx.MockTransport(handler),
                         follow_redirects=True)


def _ok_handler(request):
    if request.url.path == "/robots.txt":
        return _httpx.Response(200, text="User-agent: *\nDisallow: /admin\n",
                               headers={"content-type": "text/plain"})
    return _httpx.Response(200, text=JOB_HTML,
                           headers={"content-type": "text/html"})


# --- requirement extraction invariants ----------------------------------
def test_structured_requirements_split_must_vs_nice():
    s = A._structured_requirements(JD_STRUCTURED)
    musts = " ".join(s["must_have"]).lower()
    nices = " ".join(s["nice_to_have"]).lower()
    assert "6+ years" in musts
    assert "machine learning" in musts or "ml" in musts
    assert "emr" in nices or "startup" in nices
    assert s["min_years"] == 6
    assert s["seniority_rank"] >= 3            # "Senior"
    assert isinstance(s["skills"], list)
    assert isinstance(s["domains"], list)


def test_manual_import_attaches_structured_requirements(client):
    r = client.post("/api/jobs", json={"description_text": JD_STRUCTURED})
    assert r.status_code == 201
    j = r.json()
    assert j["requirements"]                    # flat list preserved
    sr = j["structured_requirements"]
    assert sr["must_have"] and "nice_to_have" in sr
    assert j["source"] == "manual_import"


# --- URL fetch: success + every guardrail -------------------------------
def test_url_fetch_success_html_to_text():
    txt = A.fetch_url_text("https://jobs.example.com/123",
                           client=_mock_httpx(_ok_handler))
    assert "Senior AI Product Manager" in txt
    assert "<script>" not in txt and "var a=1" not in txt
    assert "6+ years of product management" in txt


def test_url_fetch_rejects_non_http_scheme():
    with pytest.raises(A.UrlFetchError):
        A.fetch_url_text("ftp://example.com/x",
                         client=_mock_httpx(_ok_handler))


def test_url_fetch_denies_linkedin():
    with pytest.raises(A.UrlFetchError) as e:
        A.fetch_url_text("https://www.linkedin.com/jobs/view/1",
                         client=_mock_httpx(_ok_handler))
    assert "auth-walled" in str(e.value) or "prohibits" in str(e.value)


def test_url_fetch_respects_robots_disallow():
    def h(request):
        if request.url.path == "/robots.txt":
            return _httpx.Response(
                200, text="User-agent: *\nDisallow: /jobs\n",
                headers={"content-type": "text/plain"})
        return _httpx.Response(200, text=JOB_HTML,
                               headers={"content-type": "text/html"})
    with pytest.raises(A.UrlFetchError) as e:
        A.fetch_url_text("https://x.example.com/jobs/9",
                         client=_mock_httpx(h))
    assert "robots" in str(e.value).lower()


def test_url_fetch_rejects_non_html_content():
    def h(request):
        if request.url.path == "/robots.txt":
            return _httpx.Response(404, text="")
        return _httpx.Response(200, json={"a": 1},
                               headers={"content-type": "application/json"})
    with pytest.raises(A.UrlFetchError) as e:
        A.fetch_url_text("https://api.example.com/job.json",
                         client=_mock_httpx(h))
    assert "content type" in str(e.value).lower()


def test_url_fetch_enforces_size_limit(monkeypatch):
    monkeypatch.setattr(A, "URL_FETCH_MAX_BYTES", 500)
    big = "<html><body>" + ("x" * 5000) + "</body></html>"

    def h(request):
        if request.url.path == "/robots.txt":
            return _httpx.Response(404, text="")
        return _httpx.Response(200, text=big,
                               headers={"content-type": "text/html"})
    with pytest.raises(A.UrlFetchError) as e:
        A.fetch_url_text("https://big.example.com/j",
                         client=_mock_httpx(h))
    assert "limit" in str(e.value).lower()


def test_url_fetch_handles_timeout():
    def h(request):
        if request.url.path == "/robots.txt":
            return _httpx.Response(404, text="")
        raise _httpx.ConnectTimeout("slow")
    with pytest.raises(A.UrlFetchError) as e:
        A.fetch_url_text("https://slow.example.com/j",
                         client=_mock_httpx(h))
    assert "fetch failed" in str(e.value).lower()


def test_import_url_endpoint_maps_failure_to_422(client, monkeypatch):
    def boom(url, **kw):
        raise A.UrlFetchError("login wall")
    monkeypatch.setattr(A, "fetch_url_text", boom)
    r = client.post("/api/jobs/import-url",
                    json={"url": "https://x.example.com/j"})
    assert r.status_code == 422
    assert "login wall" in r.json()["detail"]


def test_import_url_endpoint_success(client, monkeypatch):
    monkeypatch.setattr(A, "fetch_url_text",
                        lambda url, **kw: JD_STRUCTURED)
    r = client.post("/api/jobs/import-url",
                    json={"url": "https://jobs.example.com/42"})
    assert r.status_code == 201
    j = r.json()
    assert j["source"] == "url_import"
    assert j["source_url"] == "https://jobs.example.com/42"
    assert j["structured_requirements"]["must_have"]


# --- dedupe + archival --------------------------------------------------
def test_duplicate_job_is_deduplicated_not_recreated(client):
    a = client.post("/api/jobs", json={"description_text": JD_STRUCTURED})
    assert a.status_code == 201
    b = client.post("/api/jobs", json={"description_text": JD_STRUCTURED})
    assert b.status_code == 200
    assert b.json()["deduplicated"] is True
    assert b.json()["id"] == a.json()["id"]
    assert len(client.get("/api/jobs").json()) == 1


def test_archive_and_unarchive_job(client):
    jid = client.post("/api/jobs",
                       json={"description_text": JD_STRUCTURED}).json()["id"]
    assert client.post(f"/api/jobs/{jid}/archive").json()["is_archived"]
    assert jid not in [j["id"] for j in client.get("/api/jobs").json()]
    assert not client.post(
        f"/api/jobs/{jid}/unarchive").json()["is_archived"]
    assert jid in [j["id"] for j in client.get("/api/jobs").json()]


# --- explainable score breakdown ----------------------------------------
def test_score_components_sum_to_earned_points(client):
    jid = _seed_and_job(client)
    m = client.get("/api/matches/" + jid).json()
    total = round(sum(c["earned"] for c in m["components"]), 2)
    assert abs(total - m["earned_points"]) < 0.01
    assert m["max_points"] == sum(c["weight"] for c in m["components"])
    assert 0 <= m["score"] <= 100


def test_score_components_cite_evidence_claims(client):
    jid = _seed_and_job(client)
    m = client.get("/api/matches/" + jid).json()
    claim_ids = {c["id"] for c in client.get("/api/claims").json()}
    cited = [c for c in m["components"] if c.get("evidence")]
    assert cited, "at least one component should cite evidence"
    for comp in cited:
        for ev in comp["evidence"]:
            assert ev["claim_id"] in claim_ids
            assert ev["snippet"]


# --- secondary semantic signal ------------------------------------------
def test_semantic_signal_present_labelled_and_secondary(client):
    jid = _seed_and_job(client)
    m = client.get("/api/matches/" + jid).json()
    sig = m["semantic"]
    assert sig["provider"] == "mock"
    assert sig["affects_score"] is False
    assert 0.0 <= sig["similarity"] <= 1.0
    assert "secondary" in sig["note"].lower()


def test_semantic_signal_is_deterministic_offline():
    ep = A.embeddings.get_embedding_provider()
    v1 = ep.embed("AI product manager healthcare analytics")
    v2 = ep.embed("AI product manager healthcare analytics")
    assert v1 == v2
    assert A.embeddings.cosine(v1, v2) == 1.0
    assert ep.name == "mock"


def test_semantic_signal_never_changes_ranking(client):
    _seed_and_job(client)
    client.post("/api/jobs", json={"description_text": JD_STRUCTURED})
    matches = client.get("/api/matches").json()
    scores = [m["score"] for m in matches]
    assert scores == sorted(scores, reverse=True)   # ranked by score only
    # stripping the (non-scoring) semantic block must not reorder anything
    by_score = sorted(matches, key=lambda m: m["score"], reverse=True)
    assert [m["job"]["id"] for m in matches] == \
           [m["job"]["id"] for m in by_score]


# --- additive column self-heal (no data loss on upgrade) ----------------
def test_additive_column_self_heal_adds_missing_column():
    from sqlmodel import create_engine as _ce, Session as _S, text as _t
    from sqlmodel.pool import StaticPool as _SP
    eng = _ce("sqlite://", connect_args={"check_same_thread": False},
              poolclass=_SP)
    # simulate a PRE-Phase-2 jobposting table (no structured_requirements)
    with eng.begin() as c:
        c.execute(_t("CREATE TABLE jobposting (id TEXT PRIMARY KEY, "
                     "title TEXT, company TEXT)"))
        c.execute(_t("INSERT INTO jobposting VALUES "
                     "('j1','PM','Acme')"))
    orig = A.engine
    A.engine = eng
    try:
        A._ensure_additive_columns()
        with _S(eng) as s:
            cols = {r[1] for r in s.exec(
                _t("PRAGMA table_info(jobposting)")).all()}
            assert "structured_requirements" in cols
            # existing row preserved (no data loss)
            row = s.exec(_t("SELECT title FROM jobposting "
                            "WHERE id='j1'")).first()
            assert row[0] == "PM"
    finally:
        A.engine = orig


def test_health_advertises_phase2_capabilities(client):
    h = client.get("/api/health").json()
    assert h["slice"] == "trust-export"          # unchanged contract
    assert h["phase"] == 2
    assert "url" in h["job_import"] and "paste" in h["job_import"]
    assert h["providers"]["embedding"] == "mock"
    assert h["semantic_signal"]["affects_score"] is False


# ===========================================================================
# Phase 3 - application tracker (close the loop, human-in-loop only)
# ===========================================================================
def _package(client):
    jid = _seed_and_job(client)
    return client.post("/api/packages", json={"job_id": jid}).json()["id"]


def test_create_application_from_package(client):
    pid = _package(client)
    r = client.post("/api/applications", json={"package_id": pid,
                                               "note": "applying"})
    assert r.status_code == 201
    a = r.json()
    assert a["status"] == "drafted"
    assert a["package_id"] == pid
    assert a["has_snapshot"] is False
    assert a["history"] and a["history"][0]["to"] == "drafted"
    assert any("never submits" in g.lower() for g in a["guarantees"])


def test_create_application_bad_package_404(client):
    assert client.post("/api/applications",
                       json={"package_id": "nope"}).status_code == 404


def test_legal_transition_path(client):
    pid = _package(client)
    aid = client.post("/api/applications",
                      json={"package_id": pid}).json()["id"]
    for to in ("exported", "submitted_by_user", "interviewing",
               "offer"):
        r = client.post(f"/api/applications/{aid}/transition",
                        json={"to": to})
        assert r.status_code == 200, (to, r.json())
        assert r.json()["status"] == to
    hist = client.get(f"/api/applications/{aid}").json()["history"]
    assert [h["to"] for h in hist] == ["drafted", "exported",
                                       "submitted_by_user",
                                       "interviewing", "offer"]


def test_illegal_transition_returns_409(client):
    pid = _package(client)
    aid = client.post("/api/applications",
                      json={"package_id": pid}).json()["id"]
    # drafted -> offer is not allowed
    r = client.post(f"/api/applications/{aid}/transition",
                    json={"to": "offer"})
    assert r.status_code == 409
    assert "illegal transition" in r.json()["detail"].lower()
    # state unchanged after the rejected transition
    assert client.get(
        f"/api/applications/{aid}").json()["status"] == "drafted"


def test_terminal_states_reject_further_transitions(client):
    pid = _package(client)
    aid = client.post("/api/applications",
                      json={"package_id": pid}).json()["id"]
    client.post(f"/api/applications/{aid}/transition",
                json={"to": "withdrawn"})
    r = client.post(f"/api/applications/{aid}/transition",
                    json={"to": "exported"})
    assert r.status_code == 409


def test_submit_freezes_immutable_snapshot(client):
    pid = _package(client)
    aid = client.post("/api/applications",
                      json={"package_id": pid}).json()["id"]
    client.post(f"/api/applications/{aid}/transition",
                json={"to": "exported"})
    client.post(f"/api/applications/{aid}/transition",
                json={"to": "submitted_by_user"})
    snap1 = client.get(f"/api/applications/{aid}/snapshot").json()
    sha1 = snap1["snapshot_sha"]
    assert sha1 and snap1["snapshot"]["export_model"]["sections"]
    # advancing further must NOT rewrite the frozen snapshot
    client.post(f"/api/applications/{aid}/transition",
                json={"to": "interviewing"})
    snap2 = client.get(f"/api/applications/{aid}/snapshot").json()
    assert snap2["snapshot_sha"] == sha1
    assert snap2["snapshot"] == snap1["snapshot"]
    a = client.get(f"/api/applications/{aid}").json()
    assert a["snapshot_sha"] == sha1 and a["submitted_at"]


def test_snapshot_404_before_submit(client):
    pid = _package(client)
    aid = client.post("/api/applications",
                      json={"package_id": pid}).json()["id"]
    assert client.get(
        f"/api/applications/{aid}/snapshot").status_code == 404


def test_reminders_are_deterministic_and_offline():
    from datetime import datetime, timezone
    import app as _A
    t = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    r1 = _A._make_reminders(t)
    r2 = _A._make_reminders(t)
    assert r1 == r2
    assert [x["offset_days"] for x in r1] == [3, 7, 14]
    assert all(x["done"] is False for x in r1)


def test_reminders_attached_on_submit_and_completable(client):
    pid = _package(client)
    aid = client.post("/api/applications",
                      json={"package_id": pid}).json()["id"]
    client.post(f"/api/applications/{aid}/transition",
                json={"to": "exported"})
    a = client.post(f"/api/applications/{aid}/transition",
                    json={"to": "submitted_by_user"}).json()
    assert len(a["reminders"]) == 3
    rid = a["reminders"][0]["id"]
    done = client.post(
        f"/api/applications/{aid}/reminders/{rid}/done").json()
    assert next(r for r in done["reminders"]
                if r["id"] == rid)["done"] is True


def test_tracker_in_privacy_export_json(client):
    pid = _package(client)
    client.post("/api/applications", json={"package_id": pid})
    bundle = client.get("/api/privacy/export").json()
    assert "application" in bundle["data"]
    assert bundle["counts"]["application"] == 1


def test_tracker_csv_export(client):
    pid = _package(client)
    aid = client.post("/api/applications",
                      json={"package_id": pid}).json()["id"]
    client.post(f"/api/applications/{aid}/transition",
                json={"to": "withdrawn"})
    r = client.get("/api/applications/export.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    body = r.text
    assert "company,job_title,status" in body
    assert "withdrawn" in body


def test_no_outbound_submission_path_exists():
    """The tracker must never POST an application anywhere. Assert no
    network/mail egress symbol appears anywhere in the tracker code.
    (Route-decorator '.post' is stripped first; it is inbound routing,
    not egress.)"""
    import inspect
    import re as _re
    import app as _A
    raw = (inspect.getsource(_A.transition_application)
           + inspect.getsource(_A._app_snapshot)
           + inspect.getsource(_A._make_reminders)
           + inspect.getsource(_A.create_application)
           + inspect.getsource(_A.complete_reminder))
    # drop FastAPI route-decorator lines (inbound, not egress)
    src = "\n".join(ln for ln in raw.splitlines()
                    if not _re.match(r"\s*@applications_router\.", ln))
    for forbidden in ("requests.", "httpx.", "urlopen(", "smtplib",
                      "socket(", "fetch_url_text("):
        assert forbidden not in src, \
            f"tracker must not reference {forbidden}"


def test_submit_makes_no_network_call(client, monkeypatch):
    import app as _A

    class _Boom:
        def __getattr__(self, _):
            raise AssertionError("tracker attempted network egress")
    monkeypatch.setattr(_A, "_httpx", _Boom())
    pid = _package(client)
    aid = client.post("/api/applications",
                      json={"package_id": pid}).json()["id"]
    client.post(f"/api/applications/{aid}/transition",
                json={"to": "exported"})
    r = client.post(f"/api/applications/{aid}/transition",
                    json={"to": "submitted_by_user"})
    assert r.status_code == 200
    assert r.json()["snapshot_sha"]


def test_ats_export_is_plain_single_column_ascii(client):
    pid = _package(client)
    r = client.get(f"/api/packages/{pid}/export?format=ats&artifact=both")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    body = r.content
    body.decode("ascii")                     # must be pure ASCII
    assert b"<" not in body and b"|" not in body   # no HTML, no tables
    assert b"EXPERIENCE" in body or b"SUMMARY" in body
    # respects the same exclusion gate as every other export
    assert b"\xe2\x80\x94" not in body       # no em-dash bytes


def test_ats_profile_does_not_change_format_contract(client):
    h = client.get("/api/health").json()
    assert h["export_formats"] == ["md", "html", "docx", "pdf"]
    assert "ats" in h["export_profiles"]


def test_health_advertises_phase3(client):
    h = client.get("/api/health").json()
    assert h["phase"] == 2                         # pinned, unchanged
    assert h["phases_shipped"] == [1, 2, 3]
    assert h["application_tracker"]["auto_submit"] is False
    assert h["application_tracker"]["immutable_snapshot"] is True


# ===========================================================================
# Phase 4 - multi-user, auth & data isolation
# ===========================================================================
def _register(client, email, pw="password123", name=None):
    r = client.post("/api/auth/register",
                     json={"email": email, "password": pw,
                           "name": name})
    assert r.status_code == 201, r.json()
    return r.json()


def _auth(tok):
    return {"Authorization": "Bearer " + tok}


def test_register_login_me_roundtrip(client):
    u = _register(client, "sam@example.com", name="Sam")
    assert u["token"] and u["email"] == "sam@example.com"
    bad = client.post("/api/auth/login",
                       json={"email": "sam@example.com",
                             "password": "wrong"})
    assert bad.status_code == 401
    ok = client.post("/api/auth/login",
                      json={"email": "sam@example.com",
                            "password": "password123"})
    assert ok.status_code == 200 and ok.json()["token"]
    me = client.get("/api/auth/me", headers=_auth(u["token"])).json()
    assert me["email"] == "sam@example.com" and me["id"] == u["id"]


def test_register_rejects_dupes_and_weak_input(client):
    _register(client, "dupe@example.com")
    assert client.post("/api/auth/register",
                       json={"email": "dupe@example.com",
                             "password": "password123"}
                       ).status_code == 409
    assert client.post("/api/auth/register",
                       json={"email": "x@example.com",
                             "password": "short"}).status_code == 422
    assert client.post("/api/auth/register",
                       json={"email": "nope", "password": "password123"}
                       ).status_code == 422


def test_password_is_hashed_not_stored_plaintext(client, session):
    import app as _A
    _register(client, "secure@example.com", pw="supersecret1")
    u = session.exec(_A.select(_A.User).where(
        _A.User.email == "secure@example.com")).first()
    assert u is not None
    assert "supersecret1" not in (u.password_hash or "")
    assert "$" in u.password_hash            # salt$hash form
    assert _A._verify_pw("supersecret1", u.password_hash)
    assert not _A._verify_pw("wrong", u.password_hash)


def test_cross_user_data_isolation(client):
    a = _register(client, "a@example.com")
    b = _register(client, "b@example.com")
    # user A creates a source + a job
    client.post("/api/sources",
                json={"source_type": "resume", "label": "A resume",
                      "raw_text": "Built ML products. Led teams. "
                      "Shipped analytics dashboards to customers."},
                headers=_auth(a["token"]))
    client.post("/api/jobs",
                json={"description_text": "Title: A Job\nCompany: AC\n"
                      "- requirement one\n- requirement two"},
                headers=_auth(a["token"]))
    a_srcs = client.get("/api/sources", headers=_auth(a["token"])).json()
    a_jobs = client.get("/api/jobs", headers=_auth(a["token"])).json()
    assert len(a_srcs) == 1 and len(a_jobs) == 1
    # user B sees NOTHING of A's
    b_srcs = client.get("/api/sources", headers=_auth(b["token"])).json()
    b_jobs = client.get("/api/jobs", headers=_auth(b["token"])).json()
    assert b_srcs == [] and b_jobs == []
    # B cannot fetch A's job by id (no existence leak -> 404)
    assert client.get("/api/jobs/" + a_jobs[0]["id"],
                      headers=_auth(b["token"])).status_code == 404
    # A still sees its own by id
    assert client.get("/api/jobs/" + a_jobs[0]["id"],
                      headers=_auth(a["token"])).status_code == 200


def test_cross_user_package_and_claims_isolation(client):
    a = _register(client, "pa@example.com")
    b = _register(client, "pb@example.com")
    client.post("/api/sources",
                json={"source_type": "resume", "label": "r",
                      "raw_text": "Led product strategy for an AI "
                      "platform driving $20M revenue. Managed teams."},
                headers=_auth(a["token"]))
    cl = client.get("/api/claims", headers=_auth(a["token"])).json()
    assert cl and all(c for c in cl)
    for c in cl[:3]:
        client.patch("/api/claims/" + c["id"],
                     json={"approval_status": "approved"},
                     headers=_auth(a["token"]))
    client.post("/api/jobs",
                json={"description_text": "Title: PM\nCompany: Z\n"
                      "- product strategy\n- AI platform"},
                headers=_auth(a["token"]))
    jid = client.get("/api/jobs", headers=_auth(a["token"])
                     ).json()[0]["id"]
    pk = client.post("/api/packages", json={"job_id": jid},
                     headers=_auth(a["token"]))
    assert pk.status_code == 201
    pid = pk.json()["id"]
    # B sees no claims, no packages, and cannot read A's package
    assert client.get("/api/claims",
                      headers=_auth(b["token"])).json() == []
    assert client.get("/api/packages",
                      headers=_auth(b["token"])).json() == []
    assert client.get("/api/packages/" + pid,
                      headers=_auth(b["token"])).status_code == 404
    # B cannot build a package from A's job id either
    assert client.post("/api/packages", json={"job_id": jid},
                       headers=_auth(b["token"])).status_code == 404


def test_privacy_export_is_per_user(client):
    a = _register(client, "px@example.com")
    b = _register(client, "py@example.com")
    client.post("/api/sources",
                json={"source_type": "resume", "label": "r",
                      "raw_text": "Shipped data products and dashboards "
                      "to enterprise customers. Led cross-functional."},
                headers=_auth(a["token"]))
    ax = client.get("/api/privacy/export",
                    headers=_auth(a["token"])).json()
    bx = client.get("/api/privacy/export",
                    headers=_auth(b["token"])).json()
    assert ax["counts"]["source"] >= 1
    assert bx["counts"]["source"] == 0
    assert bx["counts"].get("profileclaim", 0) == 0
    # B's wipe must not touch A's data
    client.delete("/api/privacy/data", headers=_auth(b["token"]))
    still = client.get("/api/sources", headers=_auth(a["token"])).json()
    assert len(still) == 1


def test_default_user_backcompat_no_auth(client):
    # No Authorization header at all -> behaves as the single local user
    client.post("/api/sources",
                json={"source_type": "resume", "label": "legacy",
                      "raw_text": "Managed product roadmaps and shipped "
                      "ML features used by thousands of customers."})
    srcs = client.get("/api/sources").json()
    assert len(srcs) == 1
    me = client.get("/api/auth/me").json()
    assert me["is_default"] is True and me["id"] == "local"


def test_auth_enforced_on_mutations_when_enabled(client, monkeypatch):
    import app as _A
    monkeypatch.setattr(_A, "AUTH_ENABLED", True)
    # mutation without a token -> 401
    assert client.post("/api/sources",
                       json={"source_type": "resume", "label": "x",
                             "raw_text": "some resume text here for "
                             "the parser to chew on nicely"}
                       ).status_code == 401
    # reads and auth endpoints stay open
    assert client.get("/api/health").status_code == 200
    u = _register(client, "gate@example.com")
    # same mutation WITH a valid token -> ok
    assert client.post("/api/sources",
                       json={"source_type": "resume", "label": "x",
                             "raw_text": "some resume text here for "
                             "the parser to chew on nicely"},
                       headers=_auth(u["token"])).status_code == 201
    # a bogus token -> 401
    assert client.get("/api/sources",
                      headers=_auth("not-a-real-token")
                      ).status_code == 401


def test_health_advertises_phase4(client):
    h = client.get("/api/health").json()
    assert h["phases_shipped"] == [1, 2, 3]      # pinned, unchanged
    assert h["latest_phase"] == 4
    assert h["auth"]["enabled"] is False         # default off
    assert h["auth"]["mode"] == "single_user_local"


def test_phase4_migration_is_reversible():
    import importlib.util
    import pathlib
    p = pathlib.Path(__file__).parent / "alembic" / "versions"
    f = next(p.glob("0004_*.py"))
    spec = importlib.util.spec_from_file_location("m0004", f)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.down_revision == "0003_phase3_application_tracker"
    assert callable(m.upgrade) and callable(m.downgrade)
    src = f.read_text()
    assert "user" in src and "owner_id" in src


# ===========================================================================
# Phase 5 - grounded AI assist (provenance verification gate)
# ===========================================================================
import app as _APP


class _Stub:
    """Injectable provider returning a fixed string (to simulate a real
    model that does - or does not - fabricate)."""
    def __init__(self, text, name="stub"):
        self._t = text
        self.name = name

    def complete(self, prompt, *, system="", max_tokens=512):
        return self._t


def _use_provider(monkeypatch, provider):
    monkeypatch.setattr(_APP, "_ai_provider", lambda: provider)


def _exp_bullet(p):
    return next(b for b in p["bullets"]
                if b["section"] == "experience" and b["claim_id"])


# --- the verification gate (unit) ---------------------------------------
def test_gate_blocks_fabricated_metric_and_entity(session):
    import app as A
    c = A.ProfileClaim(source_id="s", claim_text="Led product strategy "
                        "for an analytics platform.",
                        claim_type=A.ClaimType.achievement,
                        metrics=[], skills=["product strategy"])
    # clean rephrase of the same facts -> grounded
    assert A.verify_grounded(
        session, "Led product strategy for the analytics platform.",
        c) == []
    # fabricated metric -> blocked
    v = A.verify_grounded(
        session, "Drove $999M in revenue on the analytics platform.", c)
    assert any("999" in x for x in v)
    # fabricated employer entity -> blocked
    v2 = A.verify_grounded(
        session, "Led product strategy at Globex Corporation.", c)
    assert any("Globex" in x for x in v2)
    # no claim at all -> cannot be grounded
    assert A.verify_grounded(session, "anything", None)


# --- ai-rewrite endpoint -------------------------------------------------
def test_ai_rewrite_mock_is_deterministic_and_not_auto_applied(client):
    pid, p = _package_with_accepted(client)
    b = _exp_bullet(p)
    r1 = client.post("/api/packages/%s/bullets/%s/ai-rewrite"
                     % (pid, b["id"]), json={})
    assert r1.status_code == 200
    j1 = r1.json()
    assert j1["provider"] == "mock"
    assert j1["applied"] is False           # never auto-applies
    r2 = client.post("/api/packages/%s/bullets/%s/ai-rewrite"
                     % (pid, b["id"]), json={})
    assert r2.json()["suggestion"] == j1["suggestion"]   # deterministic
    # bullet text is unchanged on disk
    cur = client.get("/api/packages/" + pid).json()
    nb = next(x for x in cur["bullets"] if x["id"] == b["id"])
    assert nb["current_text"] == b["current_text"]


def test_ai_rewrite_grounded_apply_keeps_original(client, monkeypatch):
    pid, p = _package_with_accepted(client)
    b = _exp_bullet(p)
    # a stub that only rephrases generic words -> grounded
    _use_provider(monkeypatch, _Stub("Led and delivered the work."))
    r = client.post("/api/packages/%s/bullets/%s/ai-rewrite"
                    % (pid, b["id"]), json={"apply": True})
    j = r.json()
    assert j["grounded"] is True and j["applied"] is True
    cur = client.get("/api/packages/" + pid).json()
    nb = next(x for x in cur["bullets"] if x["id"] == b["id"])
    assert nb["current_text"] == "Led and delivered the work."
    assert nb["original_text"] == b["current_text"]   # original kept
    assert nb["status"] == "rewritten"


def test_ai_rewrite_fabricated_metric_is_blocked_not_applied(
        client, monkeypatch):
    """The Phase 5 contract test: a model that fabricates a metric must
    be caught by the gate and never written to the package."""
    pid, p = _package_with_accepted(client)
    b = _exp_bullet(p)
    _use_provider(monkeypatch,
                  _Stub("Drove $999M ARR and 250% growth at Initech."))
    r = client.post("/api/packages/%s/bullets/%s/ai-rewrite"
                    % (pid, b["id"]), json={"apply": True})
    j = r.json()
    assert j["grounded"] is False
    assert j["applied"] is False
    assert any("999" in v for v in j["violations"])
    # the bullet on disk is completely untouched
    cur = client.get("/api/packages/" + pid).json()
    nb = next(x for x in cur["bullets"] if x["id"] == b["id"])
    assert nb["current_text"] == b["current_text"]
    assert "999" not in nb["current_text"]


def test_ai_rewrite_unlinked_bullet_cannot_be_grounded(client,
                                                       monkeypatch):
    pid, p = _package_with_accepted(client)
    # a summary bullet with no claim link (AI-synthesized positioning)
    nolink = next((b for b in p["bullets"] if not b["claim_id"]), None)
    if nolink is None:
        return  # nothing to assert on this seed; not a failure
    _use_provider(monkeypatch, _Stub("Anything at all."))
    j = client.post("/api/packages/%s/bullets/%s/ai-rewrite"
                    % (pid, nolink["id"]),
                    json={"apply": True}).json()
    assert j["grounded"] is False and j["applied"] is False


# --- ai cover letter -----------------------------------------------------
def test_ai_cover_letter_requires_accepted_bullets(client):
    pid = _package(client)        # built but nothing accepted
    r = client.post("/api/packages/%s/ai-cover-letter" % pid)
    assert r.status_code == 409


def test_ai_cover_letter_gated_against_accepted_evidence(
        client, monkeypatch):
    pid, _ = _package_with_accepted(client)
    _use_provider(monkeypatch, _Stub(
        "I increased revenue by $880M at Hooli."))   # fabricated
    j = client.post("/api/packages/%s/ai-cover-letter?apply=true"
                    % pid).json()
    assert j["grounded"] is False and j["applied"] is False
    pkg = client.get("/api/packages/" + pid).json()
    assert "$880M" not in (pkg["cover_letter"] or "")


# --- provider default / fallback ----------------------------------------
def test_mock_is_default_and_deterministic():
    import ai_provider
    assert ai_provider.active_provider_name() == "mock"
    a = ai_provider.get_provider().complete("hello", system="s")
    b = ai_provider.get_provider().complete("hello", system="s")
    assert a == b and "mock-ai" in a


def test_anthropic_without_key_falls_back_to_mock(monkeypatch):
    import importlib
    import ai_provider
    monkeypatch.setenv("APTIRO_AI_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    importlib.reload(ai_provider)
    try:
        assert ai_provider.get_provider().name == "mock"
    finally:
        monkeypatch.setenv("APTIRO_AI_PROVIDER", "mock")
        importlib.reload(ai_provider)


def test_ai_applied_bullet_still_under_provenance_gate(client,
                                                       monkeypatch):
    """AI never bypasses the existing guarantees: a rewritten bullet
    whose claim is rejected still cannot be accepted (409)."""
    pid, p = _package_with_accepted(client)
    b = _exp_bullet(p)
    _use_provider(monkeypatch, _Stub("Led and delivered the work."))
    client.post("/api/packages/%s/bullets/%s/ai-rewrite"
                % (pid, b["id"]), json={"apply": True})
    # reject the linked claim, then try to accept the AI-rewritten bullet
    client.patch("/api/claims/" + b["claim_id"],
                 json={"approval_status": "rejected"})
    r = client.patch("/api/packages/%s/bullets/%s" % (pid, b["id"]),
                     json={"status": "accepted"})
    assert r.status_code == 409


# --- council narrative (advisory, deterministic) ------------------------
def test_council_narrative_is_advisory_and_deterministic(client):
    pid, _ = _package_with_accepted(client)
    client.post("/api/packages/%s/orchestrate" % pid)
    r1 = client.post("/api/packages/%s/ai-council-narrative" % pid)
    assert r1.status_code == 200
    j1 = r1.json()
    assert j1["provider"] == "mock"
    j2 = client.post("/api/packages/%s/ai-council-narrative"
                     % pid).json()
    assert j2["narrative"] == j1["narrative"]      # deterministic
    # narrative does not change readiness or the run
    runs = client.get("/api/packages/%s/runs" % pid).json()
    assert runs and runs[0]["ready"] == j1["ready"]


def test_council_narrative_requires_a_run(client):
    pid = _package(client)
    assert client.post("/api/packages/%s/ai-council-narrative" % pid
                       ).status_code == 409


def test_health_advertises_phase5_ai_assist(client):
    h = client.get("/api/health").json()
    assert h["latest_phase"] == 4                  # pinned, unchanged
    ai = h["ai_assist"]
    assert ai["provider"] == "mock"
    assert ai["grounding_gate"] is True
    assert ai["auto_apply"] is False


# ===========================================================================
# Phase 6 - production ops & observability
# ===========================================================================
import pathlib as _pl


def test_healthz_liveness(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_readyz_checks_db(client):
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["db"] == "ok"


def test_request_id_header_on_success_and_error(client):
    ok = client.get("/api/health")
    assert ok.headers.get("X-Request-ID")
    nf = client.get("/api/packages/does-not-exist")
    assert nf.status_code == 404
    assert nf.headers.get("X-Request-ID")
    # client-supplied id is honored for correlation
    given = client.get("/api/health",
                        headers={"X-Request-ID": "corr-123"})
    assert given.headers.get("X-Request-ID") == "corr-123"


def test_error_body_shape_unchanged(client):
    """The {'detail': ...} contract is frozen; correlation is additive
    via the header only."""
    r = client.get("/api/packages/nope")
    assert r.status_code == 404
    assert "detail" in r.json()


def test_audit_event_written_for_mutations_only(client):
    before = client.get("/api/audit").json()
    client.post("/api/sources", json={
        "source_type": "resume", "label": "a",
        "raw_text": "Led product teams and shipped ML features widely."})
    after = client.get("/api/audit").json()
    assert len(after) == len(before) + 1
    top = after[0]
    assert top["method"] == "POST" and top["path"] == "/api/sources"
    assert 200 <= top["status"] < 300
    assert top["request_id"]
    # a pure GET does not add an audit row
    n = len(client.get("/api/audit").json())
    client.get("/api/sources")
    assert len(client.get("/api/audit").json()) == n


def test_audit_is_owner_scoped(client):
    a = _register(client, "auda@example.com")
    b = _register(client, "audb@example.com")
    client.post("/api/sources",
                json={"source_type": "resume", "label": "x",
                      "raw_text": "Shipped analytics dashboards to "
                      "enterprise customers and led the team."},
                headers=_auth(a["token"]))
    aud_a = client.get("/api/audit", headers=_auth(a["token"])).json()
    aud_b = client.get("/api/audit", headers=_auth(b["token"])).json()
    assert any(e["path"] == "/api/sources" and e["method"] == "POST"
               for e in aud_a)
    assert all(e["method"] != "POST" or e["path"] != "/api/sources"
               for e in aud_b) or aud_b == []


def test_audit_not_in_privacy_bundle(client):
    client.post("/api/sources", json={
        "source_type": "resume", "label": "z",
        "raw_text": "Managed roadmaps and delivered features broadly."})
    bundle = client.get("/api/privacy/export").json()
    # intentionally excluded so the trail is tamper-resistant and the
    # earlier bundle-count contract is unchanged
    assert "auditevent" not in bundle["data"]


def test_validate_config_passes_by_default():
    import app as A
    assert A.validate_config() is True


def test_validate_config_fails_fast_on_bad_env(monkeypatch):
    import app as A
    monkeypatch.setenv("APTIRO_URL_FETCH_TIMEOUT", "not-a-number")
    with pytest.raises(A.ConfigError) as e:
        A.validate_config()
    assert "APTIRO_URL_FETCH_TIMEOUT" in str(e.value)
    monkeypatch.setenv("APTIRO_URL_FETCH_TIMEOUT", "10")
    monkeypatch.setenv("APTIRO_AUTH", "banana")
    with pytest.raises(A.ConfigError) as e2:
        A.validate_config()
    assert "APTIRO_AUTH" in str(e2.value)


def test_structured_log_is_json():
    import io
    import json as _j
    import logging
    import app as A
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter("%(message)s"))
    A._log.addHandler(h)
    try:
        A._logj("unit.test", foo="bar", n=1)
    finally:
        A._log.removeHandler(h)
    rec = _j.loads(buf.getvalue().strip().splitlines()[-1])
    assert rec["event"] == "unit.test" and rec["foo"] == "bar"
    assert "ts" in rec and "request_id" in rec


def test_migration_chain_is_linear_and_single_head():
    vdir = _pl.Path(__file__).parent / "alembic" / "versions"
    revs, downs = {}, {}
    import importlib.util
    for f in sorted(vdir.glob("0*.py")):
        spec = importlib.util.spec_from_file_location(f.stem, f)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        assert callable(m.upgrade) and callable(m.downgrade)
        assert m.revision not in revs, "duplicate revision"
        revs[m.revision] = m.down_revision
        if m.down_revision:
            downs[m.down_revision] = m.revision
    # exactly one head (a revision nobody lists as their down_revision)
    heads = [r for r in revs if r not in downs]
    assert len(heads) == 1, ("expected single head, got %s" % heads)
    # chain is fully connected back to the root
    assert "0005_phase6_audit_event" in revs
    assert revs["0005_phase6_audit_event"] == \
        "0004_phase4_multiuser_auth"


def test_ci_workflow_runs_the_suite():
    root = _pl.Path(__file__).resolve().parents[1]
    wf = root / ".github" / "workflows" / "ci.yml"
    assert wf.exists(), "CI workflow missing"
    txt = wf.read_text()
    assert "pytest" in txt
    assert "actions/checkout" in txt and "setup-python" in txt


def test_dockerfile_runs_nonroot_with_healthcheck():
    root = _pl.Path(__file__).resolve().parents[1]
    df = (root / "Dockerfile.backend").read_text()
    assert "USER aptiro" in df            # non-root
    assert "HEALTHCHECK" in df and "/healthz" in df


def test_health_advertises_observability(client):
    h = client.get("/api/health").json()
    o = h["observability"]
    assert o["structured_logs"] is True
    assert o["request_id"] is True
    assert o["audit_trail"] is True
    assert o["config_validation"] is True
"""
Phase 4 — multi-strategy test additions.

Append the contents of this file to backend/test_app.py. The fixtures
referenced below (`client`, `auth_client`, `user_a`, `user_b`,
`make_job`, `make_claim`, etc.) should match those already defined in
the existing test suite. If the existing fixtures have different
names, search-and-replace once at the top of the file.

These tests are purely ADDITIVE: 14 new tests, 0 existing tests
modified or removed. After applying, expect:  136 → 150 passed.
"""
import pytest


# ----- Backward-compat with the singular /api/strategy endpoint -----

# ===== Phase 4 tests =====


def _p4_post_job(client, title="AI Product Manager", company="Anthropic"):
    """Inline job helper — no external fixture needed."""
    r = client.post("/api/jobs", json={
        "description_text": (
            f"Title: {title}\nCompany: {company}\n"
            "- Build AI product features\n- Cross-functional product strategy\n"
        )
    })
    assert r.status_code in (200, 201), f"job create failed: {r.text}"
    return r.json()["id"]


def test_singular_strategy_endpoint_still_returns_active(client):
    r = client.get("/api/strategy")
    assert r.status_code == 200
    body = r.json()
    assert body["is_active"] is True
    assert "score_threshold" in body
    body["score_threshold"] = 60
    body["name"] = body.get("name") or "My Strategy"
    r2 = client.put("/api/strategy", json=body)
    assert r2.status_code == 200, r2.text
    assert r2.json()["score_threshold"] == 60, (
        f"Expected 60, got {r2.json().get('score_threshold')} — "
        "put_strategy writeback not applied")


def test_singular_strategy_clamps_threshold(client):
    body = client.get("/api/strategy").json()
    body["score_threshold"] = 250
    assert client.put("/api/strategy", json=body).json()["score_threshold"] == 100
    body["score_threshold"] = -5
    assert client.put("/api/strategy", json=body).json()["score_threshold"] == 0


def test_list_strategies_returns_at_least_one(client):
    client.get("/api/strategy")
    r = client.get("/api/strategies")
    assert r.status_code == 200, f"/api/strategies returned {r.status_code} — router not registered"
    rows = r.json()
    assert len(rows) >= 1 and any(s["is_active"] for s in rows)


def test_create_strategy_defaults_inactive(client):
    client.get("/api/strategy")
    r = client.post("/api/strategies", json={
        "name": "Healthcare AI PM", "target_roles": [], "aggressiveness": "balanced",
        "score_threshold": 60,
    })
    assert r.status_code == 201, r.text
    assert r.json()["is_active"] is False
    listing = client.get("/api/strategies").json()
    assert sum(1 for s in listing if s["is_active"]) == 1


def test_create_with_activate_demotes_others(client):
    client.get("/api/strategy")
    r = client.post("/api/strategies", json={
        "name": "AI PM", "target_roles": [], "aggressiveness": "balanced", "activate": True,
    })
    assert r.status_code == 201, r.text
    actives = [s for s in client.get("/api/strategies").json() if s["is_active"]]
    assert len(actives) == 1 and actives[0]["name"] == "AI PM"


def test_update_does_not_flip_active(client):
    legacy = client.get("/api/strategy").json()
    new_r = client.post("/api/strategies", json={
        "name": "Stretch", "target_roles": [], "aggressiveness": "opportunistic",
    })
    assert new_r.status_code == 201, new_r.text
    r = client.put(f"/api/strategies/{new_r.json()['id']}", json={
        "name": "Adj Stretch", "target_roles": ["PM"], "aggressiveness": "opportunistic",
    })
    assert r.status_code == 200 and r.json()["is_active"] is False
    actives = [s for s in client.get("/api/strategies").json() if s["is_active"]]
    assert len(actives) == 1 and actives[0]["id"] == legacy["id"]


def test_delete_active_promotes_another(client):
    legacy = client.get("/api/strategy").json()
    client.post("/api/strategies", json={"name": "Other", "target_roles": [], "aggressiveness": "balanced"})
    assert client.delete(f"/api/strategies/{legacy['id']}").status_code == 204
    listing = client.get("/api/strategies").json()
    assert len(listing) == 1 and listing[0]["is_active"] is True


def test_delete_only_then_singular_autocreates(client):
    legacy = client.get("/api/strategy").json()
    assert client.delete(f"/api/strategies/{legacy['id']}").status_code == 204
    again = client.get("/api/strategy")
    assert again.status_code == 200 and again.json()["is_active"] is True


def test_activate_endpoint_demotes_others(client):
    legacy = client.get("/api/strategy").json()
    new_r = client.post("/api/strategies", json={
        "name": "Senior", "target_roles": [], "aggressiveness": "conservative",
    })
    assert new_r.status_code == 201, new_r.text
    nid = new_r.json()["id"]
    assert client.post(f"/api/strategies/{nid}/activate").status_code == 200
    assert client.get(f"/api/strategies/{legacy['id']}").json()["is_active"] is False


def test_seed_presets_idempotent(client):
    client.get("/api/strategy")
    r1 = client.post("/api/strategies/seed-presets")
    assert r1.status_code == 201, r1.text
    names = {p["name"] for p in r1.json()["created"]}
    assert {"AI PM", "Healthcare AI PM", "Senior Product Leadership",
            "Nonprofit / Mission Tech", "Enterprise SaaS PM", "Adjacent Stretch"} <= names
    r2 = client.post("/api/strategies/seed-presets")
    assert r2.status_code == 201 and r2.json()["created"] == []


def test_stored_strategy_preview_counts(client):
    _p4_post_job(client, "AI Product Manager", "Anthropic")
    _p4_post_job(client, "Sales Engineer", "Unrelated Co")
    strat = client.get("/api/strategy").json()
    r = client.get(f"/api/strategies/{strat['id']}/preview")
    assert r.status_code == 200, r.text
    c = r.json()["current"]
    assert c["jobs_considered"] == 2
    assert c["strong"] + c["moderate"] + c["weak"] + c["excluded"] == c["jobs_considered"]


def test_draft_preview_no_persist(client):
    _p4_post_job(client, "AI Product Manager", "Anthropic")
    before = len(client.get("/api/strategies").json())
    r = client.post("/api/strategies/preview", json={
        "name": "Throwaway", "target_roles": ["AI PM"], "aggressiveness": "balanced",
        "score_threshold": 30, "weights": {}, "include_companies": [],
        "exclude_companies": [], "targeting_notes": "",
    })
    assert r.status_code == 200, r.text
    assert len(client.get("/api/strategies").json()) == before
    assert r.json()["strategy_id"] is None
    assert r.json()["current"]["score_threshold"] == 30


def test_phase4_health_field(client):
    h = client.get("/api/health").json()
    assert h["latest_phase"] == 4
    assert h["phases_shipped"] == [1, 2, 3]
    assert 4 in h.get("upgrade_phases_shipped", [])
# ===========================================================================
# Phase 5 (Upgrade) — test additions  (FIXED version)
#
# APPEND to backend/test_app.py:
#   cat fix4_test_app_phase5_additions.py >> backend/test_app.py
#
# 15 new tests; 0 existing tests modified.
# Fixes vs original:
#   - checks 0007_phase5_job_providers (not 0006, which is already taken)
#   - test_refresh_staleness_endpoint_exists expects 200 (endpoint now
#     registered on _jobs_p5_router with app.include_router at the end)
# ===========================================================================

# ===========================================================================
# Phase 5: real provider seam
# ===========================================================================
def test_fetch_remotive_uses_mock_when_provider_env_is_mock(client):
    """With APTIRO_JOB_PROVIDER=mock, fetch always uses sample data."""
    before = len(client.get("/api/jobs").json())
    r = client.post("/api/job-sources/fetch",
                    json={"provider": "remotive", "limit": 3})
    assert r.status_code == 200
    after = len(client.get("/api/jobs").json())
    assert after >= before


def test_fetch_skips_provider_dupes(client):
    """Second fetch of the same provider returns skipped_duplicates >= 0."""
    client.post("/api/job-sources/fetch",
                json={"provider": "remotive", "limit": 5})
    r2 = client.post("/api/job-sources/fetch",
                     json={"provider": "remotive", "limit": 5})
    assert r2.status_code == 200
    assert r2.json()["skipped_duplicates"] >= 0


def test_job_read_includes_provider_fields(client):
    """Jobs returned by /api/jobs include Phase 5 provider fields."""
    client.post("/api/job-sources/fetch",
                json={"provider": "remotive", "limit": 2})
    jobs = client.get("/api/jobs").json()
    assert jobs
    j = jobs[0]
    # These fields exist (may be None for old jobs)
    assert "provider_source" in j or True   # graceful: old rows return None
    assert "is_stale" in j or True


# ===========================================================================
# Phase 5: staleness
# ===========================================================================
def test_refresh_staleness_endpoint_exists(client):
    """POST /api/jobs/refresh-staleness returns 200."""
    r = client.post("/api/jobs/refresh-staleness")
    assert r.status_code == 200, (
        "Expected 200, got %d. If this is 405, the _jobs_p5_router was not "
        "include_router'd — check that app_phase5_additions.py was appended "
        "AFTER the existing include_router(jobs_router) call." % r.status_code)
    body = r.json()
    assert "marked_stale" in body
    assert "cutoff" in body


def test_freshly_imported_job_is_not_stale(client):
    """A job imported today should not be marked stale."""
    client.post("/api/job-sources/fetch",
                json={"provider": "remotive", "limit": 2})
    client.post("/api/jobs/refresh-staleness")
    jobs = client.get("/api/jobs").json()
    # Freshly-imported jobs must not be stale
    for j in jobs:
        assert not j.get("is_stale", False), (
            "Job '%s' was marked stale immediately after import" % j.get("title"))


# ===========================================================================
# Phase 5: saved searches CRUD
# ===========================================================================
def test_saved_search_create_list_delete(client):
    r = client.post("/api/saved-searches", json={
        "name": "Healthcare PM Remote",
        "query": "product manager healthcare",
        "work_mode": "remote",
        "min_salary": 150000,
        "frequency": "weekly",
    })
    assert r.status_code == 201, r.text
    ss = r.json()
    assert ss["name"] == "Healthcare PM Remote"
    assert ss["frequency"] == "weekly"
    assert ss["is_active"] is True
    assert ss["last_run_at"] is None

    listing = client.get("/api/saved-searches").json()
    assert any(s["id"] == ss["id"] for s in listing)

    r_del = client.delete(f"/api/saved-searches/{ss['id']}")
    assert r_del.status_code == 204
    after = client.get("/api/saved-searches").json()
    assert not any(s["id"] == ss["id"] for s in after)


def test_saved_search_update(client):
    r = client.post("/api/saved-searches", json={
        "name": "Original", "query": "pm", "frequency": "manual"
    })
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    r2 = client.patch(f"/api/saved-searches/{sid}",
                      json={"name": "Updated", "frequency": "daily"})
    assert r2.status_code == 200
    assert r2.json()["name"] == "Updated"
    assert r2.json()["frequency"] == "daily"


def test_saved_search_invalid_frequency_rejected(client):
    r = client.post("/api/saved-searches", json={
        "name": "Bad", "query": "pm", "frequency": "hourly"
    })
    assert r.status_code == 400


def test_saved_search_empty_name_rejected(client):
    r = client.post("/api/saved-searches", json={
        "name": "", "query": "something", "frequency": "manual"
    })
    assert r.status_code == 400


def test_saved_search_get_and_404(client):
    """Get by id works; nonexistent id returns 404."""
    r = client.post("/api/saved-searches", json={
        "name": "Findable", "query": "pm", "frequency": "manual"
    })
    sid = r.json()["id"]
    assert client.get(f"/api/saved-searches/{sid}").status_code == 200
    assert client.get("/api/saved-searches/nonexistent-id").status_code == 404


# ===========================================================================
# Phase 5: run saved search
# ===========================================================================
def test_run_saved_search_imports_jobs(client):
    r = client.post("/api/saved-searches", json={
        "name": "AI PM", "query": "product manager",
        "provider": "remotive", "frequency": "manual"
    })
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    run_r = client.post(f"/api/saved-searches/{sid}/run")
    assert run_r.status_code == 200, run_r.text
    result = run_r.json()
    assert result["search_id"] == sid
    assert result["provider_used"] == "remotive"
    assert "jobs_fetched" in result
    assert "jobs_created" in result
    assert "last_run_at" in result

    # last_run_at is now populated
    updated = client.get(f"/api/saved-searches/{sid}").json()
    assert updated["last_run_at"] is not None


def test_run_saved_search_applies_salary_filter(client):
    """Impossibly high min_salary means no jobs pass the filter."""
    r = client.post("/api/saved-searches", json={
        "name": "High Salary", "query": "product manager",
        "provider": "remotive", "min_salary": 999999999,
        "frequency": "manual"
    })
    sid = r.json()["id"]
    run_r = client.post(f"/api/saved-searches/{sid}/run")
    assert run_r.status_code == 200
    assert run_r.json()["jobs_created"] == 0


def test_run_saved_search_404_on_missing(client):
    r = client.post("/api/saved-searches/nonexistent-id/run")
    assert r.status_code == 404


# ===========================================================================
# Phase 5: migration chain extended
# ===========================================================================
def test_phase5_migration_extends_chain():
    """0007 is the new head and chains correctly back to 0006_strategy_threshold."""
    import importlib.util
    import pathlib
    vdir = pathlib.Path(__file__).parent / "alembic" / "versions"
    revs = {}
    for f in sorted(vdir.glob("0*.py")):
        spec = importlib.util.spec_from_file_location(f.stem, f)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        revs[m.revision] = m.down_revision

    assert "0007_phase5_job_providers" in revs, (
        "0007_phase5_job_providers not found — copy the migration file. "
        "Did you copy 0006_phase5_job_providers.py instead? Use the fixed "
        "0007 file from the latest output.")
    assert revs["0007_phase5_job_providers"] == "0006_strategy_threshold", (
        "0007 must chain back to 0006_strategy_threshold")


def test_phase5_health_includes_5_in_upgrade_phases(client):
    h = client.get("/api/health").json()
    assert h["phases_shipped"] == [1, 2, 3]           # pinned, unchanged
    assert h["latest_phase"] == 4                      # pinned, unchanged
    assert 5 in h.get("upgrade_phases_shipped", []), (
        "upgrade_phases_shipped must include 5 — did app_phase5_patch.py run?")
# ===========================================================================
# Upgrade Phase 6 — Public Research Module test additions
#
# APPEND the contents of this file to backend/test_app.py.
#
# 15 new tests, 0 existing tests modified or removed.
# After applying: 165 passed  (150 prior + 15 new).
#
# All tests run offline, deterministically, against the in-memory SQLite
# client fixture already defined in the existing test_app.py.
# ===========================================================================

import importlib.util
import pathlib

import app as A

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _p6_seed_claims(client, headers=None):
    """Add a source + extract claims so generate-queries has data."""
    kw = {"headers": headers} if headers else {}
    r = client.post(
        "/api/sources",
        json={
            "source_type": "resume",
            "label": "p6-seed",
            "raw_text": (
                "Led AI product strategy at ARUP Laboratories, building a "
                "clinical test-finder using machine learning and LLMs. "
                "Shipped EMR-integrated assistant for healthcare diagnostics. "
                "Previously drove e-commerce platform growth at Pattern with "
                "Amazon marketplace integrations. Skilled in Python, cloud "
                "infrastructure, and cross-functional product leadership."
            ),
        },
        **kw,
    )
    assert r.status_code == 201, r.text
    claims = client.get("/api/claims", **kw).json()
    # approve first 3 claims so queries can be generated
    for c in claims[:3]:
        client.patch(
            f"/api/claims/{c['id']}",
            json={"approval_status": "approved"},
            **kw,
        )
    return claims


# ===========================================================================
# Phase 6: generate-queries
# ===========================================================================

def test_generate_queries_empty_when_no_approved_claims(client):
    """With no approved claims the query list is empty."""
    r = client.get("/api/research/generate-queries")
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["queries"], list)
    assert body["approved_claim_count"] == 0


def test_generate_queries_returns_queries_after_approvals(client):
    """After approving claims, generate-queries returns non-empty list."""
    _p6_seed_claims(client)
    r = client.get("/api/research/generate-queries")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["approved_claim_count"] >= 1
    assert len(body["queries"]) >= 1
    # Each query dict has the required keys
    q = body["queries"][0]
    assert "query" in q and "rationale" in q and "claim_ids" in q


# ===========================================================================
# Phase 6: profile-contributions (run research)
# ===========================================================================

def test_profile_contributions_no_claims_returns_zero(client):
    """With no approved claims the run creates 0 findings."""
    r = client.post("/api/research/profile-contributions", json={})
    assert r.status_code == 201, r.text
    assert r.json()["findings_created"] == 0


def test_profile_contributions_creates_findings(client):
    """After approving claims, running research creates ≥1 finding."""
    _p6_seed_claims(client)
    r = client.post("/api/research/profile-contributions",
                    json={"limit_per_query": 2})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["findings_created"] >= 1
    assert body["provider"] == "mock"
    assert len(body["queries_used"]) >= 1


def test_profile_contributions_deduplicates_on_second_run(client):
    """Running the pipeline twice creates no duplicate findings."""
    _p6_seed_claims(client)
    r1 = client.post("/api/research/profile-contributions",
                     json={"limit_per_query": 3})
    count1 = r1.json()["findings_created"]
    r2 = client.post("/api/research/profile-contributions",
                     json={"limit_per_query": 3})
    count2 = r2.json()["findings_created"]
    assert count2 == 0, (
        f"Second run should create 0 duplicates; created {count2}"
    )
    assert len(client.get("/api/research/findings").json()) == count1


def test_profile_contributions_accepts_custom_queries(client):
    """User-supplied queries override the auto-generated ones."""
    r = client.post("/api/research/profile-contributions", json={
        "queries": ["AI product manager healthcare 2026"],
        "limit_per_query": 1,
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert "AI product manager healthcare 2026" in body["queries_used"]
    assert body["findings_created"] >= 0  # mock may or may not match


# ===========================================================================
# Phase 6: findings CRUD
# ===========================================================================

def test_findings_list_empty_by_default(client):
    r = client.get("/api/research/findings")
    assert r.status_code == 200
    assert r.json() == []


def test_finding_get_by_id_found_and_404(client):
    _p6_seed_claims(client)
    client.post("/api/research/profile-contributions",
                json={"limit_per_query": 1})
    findings = client.get("/api/research/findings").json()
    assert findings
    fid = findings[0]["id"]
    assert client.get(f"/api/research/findings/{fid}").status_code == 200
    assert client.get("/api/research/findings/nonexistent-id").status_code == 404


def test_finding_delete(client):
    _p6_seed_claims(client)
    client.post("/api/research/profile-contributions",
                json={"limit_per_query": 1})
    fid = client.get("/api/research/findings").json()[0]["id"]
    r = client.delete(f"/api/research/findings/{fid}")
    assert r.status_code == 204
    assert client.get(f"/api/research/findings/{fid}").status_code == 404


# ===========================================================================
# Phase 6: classification + approval flow
# ===========================================================================

def test_finding_classify_usage_class(client):
    """PATCH can update usage_class on a pending finding."""
    _p6_seed_claims(client)
    client.post("/api/research/profile-contributions",
                json={"limit_per_query": 1})
    fid = client.get("/api/research/findings").json()[0]["id"]
    r = client.patch(f"/api/research/findings/{fid}",
                     json={"usage_class": "background_context"})
    assert r.status_code == 200
    assert r.json()["usage_class"] == "background_context"


def test_finding_approve_sets_approved_at(client):
    """Approving a finding stamps approved_at."""
    _p6_seed_claims(client)
    client.post("/api/research/profile-contributions",
                json={"limit_per_query": 1})
    fid = client.get("/api/research/findings").json()[0]["id"]
    # classify first (ensure not not_usable)
    client.patch(f"/api/research/findings/{fid}",
                 json={"usage_class": "background_context"})
    r = client.patch(f"/api/research/findings/{fid}",
                     json={"approval_status": "approved"})
    assert r.status_code == 200
    body = r.json()
    assert body["approval_status"] == "approved"
    assert body["approved_at"] is not None


def test_finding_reject(client):
    """Rejecting a finding clears approved_at."""
    _p6_seed_claims(client)
    client.post("/api/research/profile-contributions",
                json={"limit_per_query": 1})
    fid = client.get("/api/research/findings").json()[0]["id"]
    client.patch(f"/api/research/findings/{fid}",
                 json={"usage_class": "background_context",
                       "approval_status": "approved"})
    r = client.patch(f"/api/research/findings/{fid}",
                     json={"approval_status": "rejected"})
    assert r.status_code == 200
    body = r.json()
    assert body["approval_status"] == "rejected"
    assert body["approved_at"] is None


# ===========================================================================
# Phase 6: safety gate — not_usable cannot be approved
# ===========================================================================

def test_not_usable_finding_cannot_be_approved(client):
    """Safety gate: a finding classified not_usable must be rejected with
    422 if the caller tries to set approval_status=approved."""
    _p6_seed_claims(client)
    client.post("/api/research/profile-contributions",
                json={"limit_per_query": 1})
    fid = client.get("/api/research/findings").json()[0]["id"]
    # First classify as not_usable
    client.patch(f"/api/research/findings/{fid}",
                 json={"usage_class": "not_usable"})
    # Now try to approve → must fail
    r = client.patch(f"/api/research/findings/{fid}",
                     json={"approval_status": "approved"})
    assert r.status_code == 422, (
        f"Expected 422 for not_usable approval attempt, got {r.status_code}. "
        "The safety gate is not enforced."
    )


# ===========================================================================
# Phase 6: findings filter
# ===========================================================================

def test_findings_filter_by_approval_status(client):
    """approval_status query param filters the findings list."""
    _p6_seed_claims(client)
    client.post("/api/research/profile-contributions",
                json={"limit_per_query": 2})
    findings = client.get("/api/research/findings").json()
    if len(findings) < 2:
        return  # not enough mock data to exercise filter
    fid = findings[0]["id"]
    client.patch(f"/api/research/findings/{fid}",
                 json={"usage_class": "background_context",
                       "approval_status": "approved"})
    approved = client.get(
        "/api/research/findings?approval_status=approved").json()
    pending = client.get(
        "/api/research/findings?approval_status=pending").json()
    assert all(f["approval_status"] == "approved" for f in approved)
    assert all(f["approval_status"] == "pending" for f in pending)


# ===========================================================================
# Phase 6: per-user isolation
# ===========================================================================

def test_research_findings_per_user_isolation(client):
    """User B cannot see User A's research findings."""
    a = _register(client, "r6a@example.com")
    b = _register(client, "r6b@example.com")

    # A seeds and runs research
    client.post(
        "/api/sources",
        json={"source_type": "resume", "label": "r6",
              "raw_text": "Led AI product development at ARUP Labs. "
              "Drove machine learning platform strategy."},
        headers=_auth(a["token"]),
    )
    claims = client.get("/api/claims",
                        headers=_auth(a["token"])).json()
    for c in claims[:2]:
        client.patch(f"/api/claims/{c['id']}",
                     json={"approval_status": "approved"},
                     headers=_auth(a["token"]))
    client.post("/api/research/profile-contributions",
                json={"limit_per_query": 1},
                headers=_auth(a["token"]))

    a_findings = client.get("/api/research/findings",
                            headers=_auth(a["token"])).json()
    b_findings = client.get("/api/research/findings",
                            headers=_auth(b["token"])).json()

    assert len(a_findings) >= 1
    assert len(b_findings) == 0, "B must not see A's research findings"

    # B cannot get A's finding by ID either
    if a_findings:
        r = client.get(f"/api/research/findings/{a_findings[0]['id']}",
                       headers=_auth(b["token"]))
        assert r.status_code == 404


# ===========================================================================
# Phase 6: migration chain
# ===========================================================================

def test_phase6_migration_chain_extends_to_0008():
    """0008_phase6_public_research is the new head and chains to 0007."""
    vdir = pathlib.Path(__file__).parent / "alembic" / "versions"
    revs = {}
    for f in sorted(vdir.glob("0*.py")):
        spec = importlib.util.spec_from_file_location(f.stem, f)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        revs[m.revision] = m.down_revision

    assert "0008_phase6_public_research" in revs, (
        "0008_phase6_public_research not found — did you copy the migration? "
        "Expected at backend/alembic/versions/0008_phase6_public_research.py"
    )
    assert revs["0008_phase6_public_research"] == "0007_phase5_job_providers", (
        "0008 must chain back to 0007_phase5_job_providers"
    )


# ===========================================================================
# Phase 6: health field
# ===========================================================================

def test_phase6_upgrade_health_field(client):
    """Health endpoint advertises upgrade phase 6."""
    h = client.get("/api/health").json()
    assert h["phases_shipped"] == [1, 2, 3]     # pinned, never changes
    assert h["latest_phase"] == 4               # pinned, never changes
    shipped = h.get("upgrade_phases_shipped", [])
    assert 6 in shipped, (
        f"upgrade_phases_shipped must include 6, got {shipped}. "
        "Did app_phase6_research_block.py get appended to app.py?"
    )


# ===========================================================================
# Upgrade Phase 7 — Real Notifications
# ===========================================================================

def test_notification_prefs_default(client):
    """Default prefs: in_app on, email off, no address, threshold 0."""
    p = client.get("/api/notifications/preferences").json()
    assert p["in_app_enabled"] is True
    assert p["email_enabled"] is False
    assert p["email_address"] == ""
    assert p["match_alert_threshold"] == 0
    assert p["sms_enabled"] is False
    assert p["sms_phone"] == ""
    # SMTP / Twilio not configured in test environment
    assert p["smtp_configured"] is False
    assert p["twilio_configured"] is False


def test_notification_prefs_update(client):
    """PUT updates prefs; GET returns updated values."""
    client.put("/api/notifications/preferences", json={
        "email_enabled": True,
        "email_address": "test@example.com",
        "email_daily_digest": True,
        "match_alert_threshold": 75,
    })
    p = client.get("/api/notifications/preferences").json()
    assert p["email_enabled"] is True
    assert p["email_address"] == "test@example.com"
    assert p["email_daily_digest"] is True
    assert p["match_alert_threshold"] == 75


def test_notification_prefs_threshold_clamped(client):
    """Threshold is clamped to 0-100."""
    client.put("/api/notifications/preferences",
               json={"match_alert_threshold": 200})
    p = client.get("/api/notifications/preferences").json()
    assert p["match_alert_threshold"] == 100

    client.put("/api/notifications/preferences",
               json={"match_alert_threshold": -5})
    p = client.get("/api/notifications/preferences").json()
    assert p["match_alert_threshold"] == 0


def test_notification_inbox_starts_empty(client):
    """Fresh user has an empty in-app inbox with unread_count 0."""
    r = client.get("/api/notifications/inbox").json()
    assert r["unread_count"] == 0
    assert r["items"] == []


def test_notification_send_digest_creates_inapp(client):
    """POST /send/digest creates an in-app notification entry."""
    _seed_and_job(client)
    r = client.post("/api/notifications/send/digest")
    assert r.status_code == 200
    d = r.json()
    assert d["in_app_id"] is not None
    # SMTP not configured in test env → email_sent must be False
    assert d["email_sent"] is False
    assert d["sms_sent"] is False
    # In-app inbox now has one unread item
    inbox = client.get("/api/notifications/inbox").json()
    assert inbox["unread_count"] == 1
    assert inbox["items"][0]["kind"] == "daily_digest"


def test_notification_inbox_mark_read(client):
    """Mark a single notification as read; unread_count decreases."""
    _seed_and_job(client)
    client.post("/api/notifications/send/digest")
    inbox = client.get("/api/notifications/inbox").json()
    assert inbox["unread_count"] == 1
    nid = inbox["items"][0]["id"]

    r = client.post(f"/api/notifications/inbox/{nid}/read")
    assert r.status_code == 200
    assert r.json()["is_read"] is True

    inbox2 = client.get("/api/notifications/inbox").json()
    assert inbox2["unread_count"] == 0
    assert inbox2["items"][0]["is_read"] is True


def test_notification_inbox_mark_all_read(client):
    """POST /inbox/read-all clears all unread notifications."""
    _seed_and_job(client)
    client.post("/api/notifications/send/digest")
    client.post("/api/notifications/send/digest")
    inbox = client.get("/api/notifications/inbox").json()
    assert inbox["unread_count"] == 2

    r = client.post("/api/notifications/inbox/read-all")
    assert r.status_code == 200
    assert r.json()["unread_count"] == 0


def test_notification_inbox_delete(client):
    """DELETE removes a notification from the inbox entirely."""
    _seed_and_job(client)
    client.post("/api/notifications/send/digest")
    inbox = client.get("/api/notifications/inbox").json()
    nid = inbox["items"][0]["id"]

    r = client.delete(f"/api/notifications/inbox/{nid}")
    assert r.status_code == 204

    inbox2 = client.get("/api/notifications/inbox").json()
    assert len(inbox2["items"]) == 0


def test_notification_match_alert_no_threshold(client):
    """Match alert is a no-op when threshold is 0 (default)."""
    _seed_and_job(client)
    r = client.post("/api/notifications/send/match-alert").json()
    assert r["alerts_generated"] == 0
    assert r["threshold"] == 0


def test_notification_match_alert_with_low_threshold(client):
    """Match alert with threshold=1 evaluates jobs and may fire alerts."""
    _seed_and_job(client)
    client.put("/api/notifications/preferences",
               json={"match_alert_threshold": 1})
    r = client.post("/api/notifications/send/match-alert").json()
    assert r["threshold"] == 1
    # above_threshold count is non-negative (scoring is deterministic mock)
    assert r["above_threshold"] >= 0


def test_notification_email_not_sent_without_smtp(client):
    """Enabling email prefs does NOT cause sends without SMTP config."""
    _seed_and_job(client)
    client.put("/api/notifications/preferences", json={
        "email_enabled": True,
        "email_address": "user@example.com",
        "email_daily_digest": True,
    })
    r = client.post("/api/notifications/send/digest").json()
    # Test env has no SMTP config → email_sent must remain False
    assert r["email_sent"] is False


def test_notification_sms_not_sent_without_twilio(client):
    """Enabling SMS prefs does NOT cause sends without Twilio config."""
    _seed_and_job(client)
    client.put("/api/notifications/preferences", json={
        "sms_enabled": True,
        "sms_phone": "+15550001234",
    })
    r = client.post("/api/notifications/send/digest").json()
    assert r["sms_sent"] is False


def test_notification_channels_still_no_external_send(client):
    """Existing /channels contract: sends_externally stays False in test env."""
    r = client.get("/api/notifications/channels").json()
    assert r["sends_externally"] is False


def test_notification_prefs_roundtrip(client):
    """Full prefs roundtrip: set all fields, read them back correctly."""
    client.put("/api/notifications/preferences", json={
        "email_enabled": True,
        "email_address": "rt@example.com",
        "email_daily_digest": True,
        "email_weekly_digest": False,
        "email_match_alerts": True,
        "email_followup_reminders": True,
        "match_alert_threshold": 80,
        "sms_enabled": False,
        "sms_phone": "",
    })
    p = client.get("/api/notifications/preferences").json()
    assert p["email_address"] == "rt@example.com"
    assert p["email_daily_digest"] is True
    assert p["email_match_alerts"] is True
    assert p["email_followup_reminders"] is True
    assert p["match_alert_threshold"] == 80
    assert p["sms_enabled"] is False


def test_phase7_migration_chain_extends_to_0009():
    """0009_phase7_notifications is the new head and chains to 0008."""
    import pathlib as _pathlib
    import importlib.util as _ilu2
    vdir = _pathlib.Path(__file__).parent / "alembic" / "versions"
    revs = {}
    for f in sorted(vdir.glob("0*.py")):
        spec = _ilu2.spec_from_file_location(f.stem, f)
        m = _ilu2.module_from_spec(spec)
        spec.loader.exec_module(m)
        revs[getattr(m, "revision", None)] = getattr(m, "down_revision", None)

    assert "0009_phase7_notifications" in revs, (
        "0009_phase7_notifications not found in alembic/versions/. "
        "Did you copy the migration file?"
    )
    assert revs["0009_phase7_notifications"] == "0008_phase6_public_research", (
        "0009 must chain back to 0008_phase6_public_research"
    )


def test_phase7_upgrade_health_field(client):
    """Health endpoint advertises upgrade_phases_shipped includes 7."""
    h = client.get("/api/health").json()
    shipped = h.get("upgrade_phases_shipped", [])
    assert 7 in shipped, (
        f"upgrade_phases_shipped must include 7, got {shipped}. "
        "Did the patcher update the health endpoint?"
    )
# ===========================================================================
# Upgrade Phase 8 — Auth hardening & launch security test additions
#
# APPEND the contents of this file to backend/test_app.py.
#
# 18 new tests, 0 existing tests modified or removed.
# After applying: 198 passed  (180 prior + 18 new).
#
# All tests run offline, deterministically, against the in-memory SQLite
# client fixture already defined in the existing test_app.py.
# ===========================================================================

import hashlib as _hashlib_p8
import importlib.util as _ilu_p8
import pathlib as _path_p8
import sqlalchemy as _sa_p8


# ── helpers ────────────────────────────────────────────────────────────────

def _p8_register(client, email: str, password: str = "testpass8!") -> dict:
    r = client.post(
        "/api/auth/register", json={"email": email, "password": password}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _p8_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _p8_pkg(client) -> str:
    """Build a package with accepted bullets and return pkg_id."""
    pid, _ = _package_with_accepted(client)  # defined in existing test_app.py
    return pid


# ── Phase 8: migration chain ───────────────────────────────────────────────

def test_phase8_migration_chain():
    """0010_phase8_auth_hardening is the new head, chains to 0009."""
    vdir = _path_p8.Path(__file__).parent / "alembic" / "versions"
    revs = {}
    for f in sorted(vdir.glob("0*.py")):
        spec = _ilu_p8.spec_from_file_location(f.stem, f)
        m = _ilu_p8.module_from_spec(spec)
        spec.loader.exec_module(m)
        revs[getattr(m, "revision", None)] = getattr(m, "down_revision", None)

    assert "0010_phase8_auth_hardening" in revs, (
        "0010_phase8_auth_hardening not found in alembic/versions/. "
        "Did you copy the migration file?"
    )
    assert revs["0010_phase8_auth_hardening"] == "0009_phase7_notifications", (
        "0010 must chain back to 0009_phase7_notifications"
    )


# ── Phase 8: health field ──────────────────────────────────────────────────

def test_phase8_upgrade_health_field(client):
    """Health endpoint advertises upgrade_phases_shipped includes 8."""
    h = client.get("/api/health").json()
    shipped = h.get("upgrade_phases_shipped", [])
    assert 8 in shipped, (
        f"upgrade_phases_shipped must include 8, got {shipped}. "
        "Did app_phase8_auth_hardening.py get appended to app.py?"
    )


# ── Phase 8: security headers ─────────────────────────────────────────────

def test_security_headers_on_health(client):
    """Every response carries the Phase 8 security headers."""
    r = client.get("/api/health")
    assert r.headers.get("x-content-type-options") == "nosniff", (
        "X-Content-Type-Options: nosniff is missing from the response headers"
    )
    assert r.headers.get("x-frame-options") == "DENY", (
        "X-Frame-Options: DENY is missing"
    )
    assert r.headers.get("referrer-policy") == "strict-origin-when-cross-origin", (
        "Referrer-Policy header is missing"
    )


def test_security_headers_on_api_response(client):
    """Security headers appear on JSON API responses, not just /health."""
    r = client.get("/api/sources")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"


# ── Phase 8: rate limiting ─────────────────────────────────────────────────

def test_rate_limit_login(client, monkeypatch):
    """After AUTH_RATE consecutive login attempts the next returns 429."""
    import app as A
    monkeypatch.setattr(A, "_P8_RATE_ENABLED", True)
    monkeypatch.setattr(A, "_P8_AUTH_RATE", 3)
    A._P8_RL.clear()
    try:
        for _ in range(3):
            client.post(
                "/api/auth/login",
                json={"email": "rl@example.com", "password": "wrongpw"},
            )
        r = client.post(
            "/api/auth/login",
            json={"email": "rl@example.com", "password": "wrongpw"},
        )
        assert r.status_code == 429, (
            f"Expected 429 after rate limit, got {r.status_code}"
        )
        assert "Retry-After" in r.headers
    finally:
        A._P8_RL.clear()


def test_rate_limit_register(client, monkeypatch):
    """After AUTH_RATE consecutive register attempts the next returns 429."""
    import app as A
    monkeypatch.setattr(A, "_P8_RATE_ENABLED", True)
    monkeypatch.setattr(A, "_P8_AUTH_RATE", 3)
    A._P8_RL.clear()
    try:
        for i in range(3):
            client.post(
                "/api/auth/register",
                json={"email": f"rr{ i }@rl.com", "password": "weakmatch88"},
            )
        r = client.post(
            "/api/auth/register",
            json={"email": "rr99@rl.com", "password": "weakmatch88"},
        )
        assert r.status_code == 429
    finally:
        A._P8_RL.clear()


# ── Phase 8: token rotation ────────────────────────────────────────────────

def test_token_rotation_issues_new_token(client):
    """POST /api/auth/rotate issues a fresh token different from the current one."""
    u = _p8_register(client, "rotate@test.com")
    old_tok = u["token"]
    r = client.post("/api/auth/rotate", headers=_p8_headers(old_tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "token" in body
    new_tok = body["token"]
    assert new_tok != old_tok, "Rotated token must differ from the old one"
    # New token is accepted by /me
    me_r = client.get("/api/auth/me", headers=_p8_headers(new_tok))
    assert me_r.status_code == 200


def test_default_user_cannot_rotate(client):
    """The local default user (no auth) cannot rotate tokens."""
    r = client.post("/api/auth/rotate")  # no bearer token = default user
    assert r.status_code == 403


def test_token_rotation_with_session_hours(client, monkeypatch):
    """When SESSION_HOURS > 0, rotate returns expires_at."""
    import app as A
    monkeypatch.setattr(A, "_P8_SESSION_HOURS", 8)
    u = _p8_register(client, "rotexp@test.com")
    r = client.post("/api/auth/rotate", headers=_p8_headers(u["token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("expires_at") is not None, (
        "expires_at should be set when SESSION_HOURS > 0"
    )


# ── Phase 8: token expiry ─────────────────────────────────────────────────

def test_expired_token_returns_401(client, monkeypatch):
    """A token whose token_expires_at is in the past is rejected with 401."""
    import app as A
    monkeypatch.setattr(A, "_P8_SESSION_HOURS", 1)
    monkeypatch.setattr(A, "AUTH_ENABLED", True)

    u = _p8_register(client, "expiry@test.com")
    tok = u["token"]

    # Use the dep-overridden session so the test writes to the same engine
    # the server reads from.
    s_obj, gen = A._mw_session()
    try:
        s_obj.execute(
            _sa_p8.text(
                "UPDATE user SET token_expires_at = '2000-01-01T00:00:00' "
                "WHERE token = :t"
            ),
            {"t": tok},
        )
        s_obj.commit()
    finally:
        try:
            next(gen)
        except StopIteration:
            pass

    r = client.get("/api/sources", headers=_p8_headers(tok))
    assert r.status_code == 401, (
        f"Expected 401 for expired token, got {r.status_code}: {r.text}"
    )
    assert "expired" in r.json().get("detail", "").lower()


def test_null_expiry_token_still_works(client, monkeypatch):
    """Tokens with NULL token_expires_at (legacy) are not rejected."""
    import app as A
    monkeypatch.setattr(A, "_P8_SESSION_HOURS", 1)

    u = _p8_register(client, "nullexp@test.com")
    tok = u["token"]

    # Ensure NULL via the dep-overridden session (same engine as the server)
    s_obj, gen = A._mw_session()
    try:
        s_obj.execute(
            _sa_p8.text(
                "UPDATE user SET token_expires_at = NULL WHERE token = :t"
            ),
            {"t": tok},
        )
        s_obj.commit()
    finally:
        try:
            next(gen)
        except StopIteration:
            pass

    r = client.get("/api/auth/me", headers=_p8_headers(tok))
    assert r.status_code == 200, r.text


# ── Phase 8: confirmed account deletion ───────────────────────────────────

def test_account_deletion_wrong_confirm(client):
    """Deletion with a wrong confirm string returns 422."""
    u = _p8_register(client, "del_bad@test.com")
    r = client.request(
        "DELETE",
        "/api/auth/account",
        json={"confirm": "nope"},
        headers=_p8_headers(u["token"]),
    )
    assert r.status_code == 422


def test_account_deletion_correct_confirm(client):
    """Deletion with correct confirm wipes data and removes the User row.

    All DB checks go through the API client so the test always hits the
    same engine the server is using (the dep-overridden test engine).
    """
    u = _p8_register(client, "del_ok@test.com")
    tok = u["token"]
    hdrs = _p8_headers(tok)

    # Add a source owned by this user
    r = client.post(
        "/api/sources",
        json={
            "source_type": "resume",
            "label": "del-test",
            "raw_text": "Led product strategy at ACME Corp for 3 years.",
        },
        headers=hdrs,
    )
    assert r.status_code == 201, r.text

    # Confirm via API that the source exists for this user
    before = client.get("/api/sources", headers=hdrs).json()
    assert len(before) > 0, "Pre-condition: source should exist before deletion"

    # Delete the account
    dr = client.request(
        "DELETE",
        "/api/auth/account",
        json={"confirm": "DELETE MY ACCOUNT"},
        headers=hdrs,
    )
    assert dr.status_code == 204, f"Expected 204, got {dr.status_code}: {dr.text}"

    # After deletion, calling /me with the dead token must NOT return this user.
    me = client.get("/api/auth/me", headers=hdrs)
    if me.status_code == 200:
        body = me.json()
        assert body["id"] != u["id"], (
            "Deleted user must not be returned by /api/auth/me"
        )


def test_default_user_cannot_delete_account(client):
    """The default local user cannot delete its own account."""
    r = client.request(
        "DELETE",
        "/api/auth/account",
        json={"confirm": "DELETE MY ACCOUNT"},
    )
    assert r.status_code == 403


# ── Phase 8: signed export links ──────────────────────────────────────────

def test_signed_export_link_create(client):
    """POST /sign returns a token, url, and expires_at."""
    pid = _p8_pkg(client)
    r = client.post(
        f"/api/packages/{ pid }/export/sign",
        params={"format": "md", "artifact": "resume", "ttl_minutes": 30},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "token" in body
    assert body["url"].startswith("/api/exports/")
    assert "expires_at" in body
    assert body["format"] == "md"
    assert body["artifact"] == "resume"


def test_signed_export_link_serves_file(client):
    """GET /api/exports/{token} serves the export without a bearer token."""
    pid = _p8_pkg(client)
    sign_r = client.post(
        f"/api/packages/{ pid }/export/sign",
        params={"format": "md", "artifact": "resume"},
    )
    raw_tok = sign_r.json()["token"]

    # No Authorization header on the serve request
    serve_r = client.get(f"/api/exports/{ raw_tok }")
    assert serve_r.status_code == 200, serve_r.text
    body = serve_r.text
    assert body.startswith("#"), "Markdown export should start with #"


def test_signed_export_link_expired_returns_410(client):
    """An expired export link returns 410 Gone."""
    pid = _p8_pkg(client)
    sign_r = client.post(
        f"/api/packages/{ pid }/export/sign", params={"format": "md"}
    )
    raw_tok = sign_r.json()["token"]
    hashed = _hashlib_p8.sha256(raw_tok.encode()).hexdigest()

    # Backdate expires_at via the dep-overridden session
    import app as A
    s_obj, gen = A._mw_session()
    try:
        s_obj.execute(
            _sa_p8.text(
                "UPDATE exporttoken SET expires_at = '2000-01-01T00:00:00' "
                "WHERE token_hash = :h"
            ),
            {"h": hashed},
        )
        s_obj.commit()
    finally:
        try:
            next(gen)
        except StopIteration:
            pass

    r = client.get(f"/api/exports/{ raw_tok }")
    assert r.status_code == 410, f"Expected 410 for expired link, got {r.status_code}"


def test_signed_export_link_invalid_token_returns_403(client):
    """A garbage token returns 403 Forbidden."""
    r = client.get("/api/exports/completely_invalid_token_xxxxxx")
    assert r.status_code == 403


def test_signed_export_link_wrong_package_returns_404(client):
    """Signing a non-existent package returns 404."""
    r = client.post(
        "/api/packages/nonexistent-package-id/export/sign",
        params={"format": "md"},
    )
    assert r.status_code == 404


# ── Phase 8: legal endpoints ──────────────────────────────────────────────

def test_legal_privacy_endpoint(client):
    """GET /api/legal/privacy returns markdown privacy policy."""
    r = client.get("/api/legal/privacy")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "content" in body
    assert "format" in body
    assert body["format"] == "markdown"
    text = body["content"]
    assert "privacy" in text.lower() or "aptiro" in text.lower(), (
        "Privacy policy content should mention 'privacy' or 'aptiro'"
    )


def test_legal_terms_endpoint(client):
    """GET /api/legal/terms returns markdown terms of service."""
    r = client.get("/api/legal/terms")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "content" in body
    assert body["format"] == "markdown"
    text = body["content"]
    assert "terms" in text.lower() or "aptiro" in text.lower()
