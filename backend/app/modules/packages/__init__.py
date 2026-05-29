"""Aptiro backend module: `app.modules.packages` (Phase 9 PR-12).

The packages/runs HTTP layer, extracted VERBATIM from legacy.py:
  * Router  — packages_router  (prefix=/api/packages)
  * Router  — runs_router      (/api/runs/...)
  * Every endpoint decorated by those two routers — package CRUD, bullet
    patch, the 13-step orchestrator + 5-agent council, the Phase-5 grounded
    AI-assist endpoints, and the export / export-preview endpoints.

Behaviour is identical to pre-PR-12. legacy.py re-imports the two routers
so `app.include_router(packages_router)` / `runs_router` register exactly
the same routes as before, and the test contract
(`import app as A; A.packages_router`, `A.runs_router`) is unchanged.

NOT moved (imported back from legacy by the block below):
  * Table models   — ApplicationPackage, PackageBullet, AgentRun,
                     AgentCritique (their relationship annotations use the
                     actual class, so they must stay co-located for now).
  * Enums + schemas — PackageStatus/BulletStatus/RunStatus/AgentRole,
                     PackageOut/BulletOut/RunOut/... and the AI-assist
                     request/response schemas.
  * Helpers        — build_package, _package_out, _bullet_out, _run_out,
                     _bullet_live_provenance, _unsupported_metrics,
                     _recompute_cover_letter, _ai_provider, _export_model,
                     EXPORT_MEDIA, _get_owned, _active_strategy, score_job,
                     exporting, provenance_color, JobPosting, _uid,
                     get_session, _now, ...

Cross-module load order
───────────────────────
legacy imports this module from the line directly above
`app.include_router(packages_router)`. By then legacy has already defined
every name imported below (models/schemas/helpers are all earlier in the
file, and the export helpers sit just above the include call), so the
partial-module import resolves with no circular hazard.

# APTIRO_PHASE9_PR12_PACKAGES_MARKER
"""

import re  # noqa: F401
from datetime import datetime, timezone  # noqa: F401
from typing import Any, Dict, List, Optional  # noqa: F401

from fastapi import (  # noqa: F401
    APIRouter, Body, Depends, File, Form, HTTPException, Path, Query,
    Response, UploadFile, status,
)
from pydantic import BaseModel  # noqa: F401
from sqlmodel import (  # noqa: F401
    Field, Relationship, SQLModel, Session, select,
)

# Names that stay in legacy.py (models, schemas, helpers) but are
# referenced by the moved router layer. Imported from the
# partially-loaded legacy module — every name below is defined
# earlier in legacy than the line that imports this module.
from app.legacy import (  # noqa: E402,F401
    AICoverLetterOut,
    AIRewriteOut,
    AIRewriteRequest,
    AgentRun,
    ApplicationPackage,
    BulletOut,
    BulletPatch,
    BulletStatus,
    CouncilNarrativeOut,
    EXPORT_MEDIA,
    JobPosting,
    PackageBullet,
    PackageCreate,
    PackageOut,
    ProfileClaim,
    ProvenanceCategory,
    RunListItem,
    RunOut,
    _active_strategy,
    _bullet_live_provenance,
    _bullet_out,
    _export_model,
    _get_owned,
    _grounding_text,
    _now,
    _package_out,
    _recompute_cover_letter,
    _run_out,
    _uid,
    build_package,
    exporting,
    get_session,
    orchestrate_package,
    verify_grounded,
)

# ── Test seams ──────────────────────────────────────────────
# These names are monkeypatched by the test-suite on the `app`
# proxy, which forwards writes to app.legacy. We import the
# legacy MODULE (not the names) and delegate on every call, so a
# stub injected by a test is always seen here. Binding these by
# value with `from app.legacy import ...` would snapshot the
# original and silently defeat the seam.
import app.legacy as _aptiro_legacy  # noqa: E402

def _ai_provider(*args, **kwargs):  # noqa: E302
    return _aptiro_legacy._ai_provider(*args, **kwargs)


packages_router = APIRouter(prefix="/api/packages", tags=["packages"])

runs_router = APIRouter(tags=["runs"])

@packages_router.get("", response_model=List[PackageOut])
def list_packages(session: Session = Depends(get_session)):
    rows = session.exec(select(ApplicationPackage).where(
        ApplicationPackage.owner_id == _uid()).order_by(
        ApplicationPackage.created_at.desc())).all()
    return [_package_out(session, p) for p in rows]

@packages_router.post("", response_model=PackageOut, status_code=201)
def create_package(body: PackageCreate,
                   session: Session = Depends(get_session)):
    job = _get_owned(session, JobPosting, body.job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    pkg = build_package(session, job, _active_strategy(session))
    return _package_out(session, pkg)

@packages_router.get("/{pkg_id}", response_model=PackageOut)
def get_package(pkg_id: str, session: Session = Depends(get_session)):
    pkg = _get_owned(session, ApplicationPackage, pkg_id)
    if not pkg:
        raise HTTPException(404, "Package not found")
    return _package_out(session, pkg)

@packages_router.patch("/{pkg_id}/bullets/{bid}",
                       response_model=BulletOut)
def patch_bullet(pkg_id: str, bid: str, body: BulletPatch,
                 session: Session = Depends(get_session)):
    pkg = session.get(ApplicationPackage, pkg_id)
    if not pkg:
        raise HTTPException(404, "Package not found")
    b = session.get(PackageBullet, bid)
    if not b or b.package_id != pkg_id:
        raise HTTPException(404, "Bullet not found")
    if body.user_note is not None:
        b.user_note = body.user_note
    if body.current_text is not None and \
            body.current_text != b.current_text:
        b.current_text = body.current_text
        if b.status not in (BulletStatus.locked,):
            b.status = BulletStatus.rewritten
    if body.status is not None:
        if body.status == BulletStatus.accepted:
            live = _bullet_live_provenance(session, b)
            if live == ProvenanceCategory.unsupported:
                raise HTTPException(
                    409, "Cannot accept a bullet with no approved "
                         "backing. Approve the source claim or rewrite "
                         "this bullet.")
        b.status = body.status
    b.updated_at = _now()
    session.add(b)
    session.commit()
    session.refresh(b)
    pkg = session.get(ApplicationPackage, pkg_id)
    if any(x.section == "cover_letter" for x in pkg.bullets):
        _recompute_cover_letter(session, pkg)
        session.commit()
    session.refresh(b)
    return _bullet_out(session, b)

@packages_router.post("/{pkg_id}/bullets/{bid}/ai-rewrite",
                      response_model=AIRewriteOut)
def ai_rewrite_bullet(pkg_id: str, bid: str, body: AIRewriteRequest,
                      session: Session = Depends(get_session)):
    pkg = _get_owned(session, ApplicationPackage, pkg_id)
    if not pkg:
        raise HTTPException(404, "Package not found")
    b = session.get(PackageBullet, bid)
    if not b or b.package_id != pkg_id:
        raise HTTPException(404, "Bullet not found")
    if b.status == BulletStatus.locked:
        raise HTTPException(409, "Bullet is locked.")
    claim = session.get(ProfileClaim, b.claim_id) if b.claim_id else None
    src = _grounding_text(session, claim)
    sys = ("You are a careful resume editor. Rewrite the bullet to be "
           "crisp and outcome-led, but you may ONLY use facts present "
           "in the EVIDENCE. Do not introduce any company, product, "
           "metric, number, title, or date that is not in the EVIDENCE. "
           "Return only the rewritten bullet, one line.")
    prompt = ("EVIDENCE:\n%s\n\nCURRENT BULLET:\n%s\n\n%s\nRewrite:"
              % (src or "(none)", b.current_text or "",
                 (body.instruction or "").strip()))
    try:
        prov = _ai_provider()
        suggestion = (prov.complete(prompt, system=sys,
                                    max_tokens=300) or "").strip()
    except Exception as e:
        raise HTTPException(
            502, "AI provider error (%s); no change made."
            % type(e).__name__)
    violations = verify_grounded(session, suggestion, claim)
    grounded = not violations
    applied = False
    if body.apply and grounded and suggestion:
        b.original_text = b.original_text or b.current_text
        b.current_text = suggestion
        if b.status != BulletStatus.locked:
            b.status = BulletStatus.rewritten
        b.updated_at = _now()
        session.add(b)
        session.commit()
        if any(x.section == "cover_letter" for x in pkg.bullets):
            _recompute_cover_letter(session, pkg)
            session.commit()
        applied = True
    return AIRewriteOut(
        provider=prov.name, suggestion=suggestion, grounded=grounded,
        violations=violations, applied=applied,
        original_text=b.original_text or b.current_text)

@packages_router.post("/{pkg_id}/ai-cover-letter",
                      response_model=AICoverLetterOut)
def ai_cover_letter(pkg_id: str, apply: bool = False,
                    session: Session = Depends(get_session)):
    pkg = _get_owned(session, ApplicationPackage, pkg_id)
    if not pkg:
        raise HTTPException(404, "Package not found")
    accepted = [b for b in pkg.bullets
                if b.section in ("experience", "summary", "skills")
                and b.status in (BulletStatus.accepted,
                                 BulletStatus.locked)]
    if not accepted:
        raise HTTPException(
            409, "No accepted bullets to ground a cover letter on. "
            "Accept some evidence-backed bullets first.")
    facts = "\n".join("- " + (b.current_text or "") for b in accepted)
    sys = ("You are a careful cover-letter writer. Use ONLY the facts "
           "in EVIDENCE. Do not introduce any company, product, metric, "
           "number, title, or date not present in EVIDENCE. 120 words "
           "max, professional, no placeholders.")
    prompt = ("ROLE: %s at %s\n\nEVIDENCE (accepted, evidence-backed "
              "bullets):\n%s\n\nWrite the cover letter body:"
              % (pkg.title or "the role", pkg.company or "the company",
                 facts))
    try:
        prov = _ai_provider()
        draft = (prov.complete(prompt, system=sys,
                               max_tokens=400) or "").strip()
    except Exception as e:
        raise HTTPException(
            502, "AI provider error (%s); no change made."
            % type(e).__name__)
    # Gate the draft against the UNION of the accepted bullets' claims.
    src_terms = []
    for b in accepted:
        c = session.get(ProfileClaim, b.claim_id) if b.claim_id else None
        src_terms.append(_grounding_text(session, c))
        src_terms.append((b.current_text or "").lower())
    union = " ".join(src_terms)

    class _Syn:
        claim_text = union
        metrics = []
        skills = []
        company = pkg.company or ""
        role = pkg.title or ""
        date_range = ""
    violations = verify_grounded(session, draft, _Syn())
    grounded = not violations
    applied = False
    if apply and grounded and draft:
        pkg.cover_letter = draft
        pkg.updated_at = _now()
        session.add(pkg)
        session.commit()
        applied = True
    return AICoverLetterOut(
        provider=prov.name, draft=draft, grounded=grounded,
        violations=violations, applied=applied)

@packages_router.post("/{pkg_id}/orchestrate", response_model=RunOut,
                      status_code=201)
def run_orchestrator(pkg_id: str,
                     session: Session = Depends(get_session)):
    pkg = session.get(ApplicationPackage, pkg_id)
    if not pkg:
        raise HTTPException(404, "Package not found")
    run = orchestrate_package(session, pkg)
    return _run_out(session, run)

@packages_router.get("/{pkg_id}/runs", response_model=List[RunListItem])
def list_runs(pkg_id: str, session: Session = Depends(get_session)):
    pkg = session.get(ApplicationPackage, pkg_id)
    if not pkg:
        raise HTTPException(404, "Package not found")
    rs = sorted(pkg.runs, key=lambda r: r.started_at, reverse=True)
    return [RunListItem(
        id=r.id, status=r.status, ready=r.ready, summary=r.summary,
        critique_count=len(r.critiques), created_at=r.started_at)
        for r in rs]

@packages_router.post("/{pkg_id}/ai-council-narrative",
                      response_model=CouncilNarrativeOut)
def ai_council_narrative(pkg_id: str,
                         session: Session = Depends(get_session)):
    pkg = _get_owned(session, ApplicationPackage, pkg_id)
    if not pkg:
        raise HTTPException(404, "Package not found")
    runs = sorted(pkg.runs, key=lambda r: r.started_at, reverse=True)
    if not runs:
        raise HTTPException(
            409, "Run the council first (orchestrate the package).")
    run = runs[0]
    lines = ["%s/%s: %s" % (c.agent, c.severity, c.message)
             for c in run.critiques]
    sys = ("Summarize this resume-package review for the candidate in "
           "2-3 plain sentences. Summarize ONLY the findings listed; do "
           "not invent issues, metrics, or praise not in the list.")
    prompt = ("READY: %s\nFINDINGS:\n%s\n\nSummary:"
              % (run.ready, "\n".join(lines) or "(no critiques)"))
    try:
        prov = _ai_provider()
        narrative = (prov.complete(prompt, system=sys,
                                   max_tokens=250) or "").strip()
    except Exception as e:
        raise HTTPException(
            502, "AI provider error (%s)." % type(e).__name__)
    return CouncilNarrativeOut(provider=prov.name, narrative=narrative,
                               ready=run.ready)

@runs_router.get("/api/runs/{run_id}", response_model=RunOut)
def get_run(run_id: str, session: Session = Depends(get_session)):
    run = session.get(AgentRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return _run_out(session, run)

@packages_router.get("/{pkg_id}/export/preview")
def export_preview(pkg_id: str, include_unsupported: bool = False,
                   session: Session = Depends(get_session)):
    pkg = session.get(ApplicationPackage, pkg_id)
    if not pkg:
        raise HTTPException(404, "Package not found")
    return _export_model(session, pkg, include_unsupported)

@packages_router.get("/{pkg_id}/export")
def export_package(pkg_id: str,
                   format: str = Query("md"),
                   artifact: str = Query("resume"),
                   include_unsupported: bool = Query(False),
                   session: Session = Depends(get_session)):
    pkg = session.get(ApplicationPackage, pkg_id)
    if not pkg:
        raise HTTPException(404, "Package not found")
    if artifact not in ("resume", "cover_letter", "both"):
        raise HTTPException(400, "artifact must be resume|cover_letter|both")
    model = _export_model(session, pkg, include_unsupported)
    if format == exporting.ATS_PROFILE:
        content, ext = exporting.render_ats(model, artifact)
        safe = re.sub(r"[^A-Za-z0-9]+", "_",
                      "%s_%s_ats" % (pkg.company or "aptiro",
                                     artifact)).strip("_")
        return Response(
            content=content, media_type="text/plain; charset=us-ascii",
            headers={"Content-Disposition":
                     'attachment; filename="%s.%s"'
                     % (safe or "aptiro_package", ext)})
    if format not in exporting.FORMATS:
        raise HTTPException(
            400, "Unsupported format. Choose one of: %s (or 'ats')"
            % ", ".join(exporting.FORMATS))
    try:
        content, ext = exporting.render(model, format, artifact)
    except exporting.ExportUnavailable as e:
        raise HTTPException(501, str(e))
    safe = re.sub(r"[^A-Za-z0-9]+", "_",
                  "%s_%s" % (pkg.company or "aptiro", artifact)).strip("_")
    fname = "%s.%s" % (safe or "aptiro_package", ext)
    return Response(
        content=content, media_type=EXPORT_MEDIA.get(ext,
                                                     "application/octet-stream"),
        headers={"Content-Disposition": 'attachment; filename="%s"' % fname})
