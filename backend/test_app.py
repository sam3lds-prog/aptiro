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
