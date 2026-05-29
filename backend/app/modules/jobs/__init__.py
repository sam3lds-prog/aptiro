"""Aptiro backend module: `app.modules.jobs`.

Phase 9 PR-9 — job domain extracted from legacy.py:
  * Enum      — WorkMode
  * Model     — JobPosting
  * Schemas   — JobImportRequest, UrlImportRequest, JobRead
  * Regex     — _TITLE_HINT, _COMPANY_HINT, _LOCATION_HINT, _SALARY
  * Helpers   — _first, _money, _detect_work_mode,
                _find_duplicate, _job_read, import_job
  * Router    — jobs_router with list, create, import-url endpoints

Behavior is identical to pre-PR-9. legacy.py re-imports every moved
name so the test contract (`import app as A; A.JobPosting`,
`A.WorkMode`, `A.jobs_router`, `A.import_job`, etc.) is unchanged.

Cross-module dependencies
─────────────────────────
  At module load:
    app.core.config            DEFAULT_UID
    app.core.identity          _uid
    app.db.engine              _now, get_session
  At runtime (lazy — these still live in legacy):
    app.legacy                 _extract_requirements,
                               _structured_requirements
    These are parsing helpers that are still in legacy until the
    parsing PR; import_job uses them inside its function body, so
    legacy is fully loaded by the time they are needed.

# APTIRO_PHASE9_PR9_JOBS_MARKER
"""

import re
import uuid as _uuidmod
from datetime import datetime
from enum import Enum
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import Column, JSON
from sqlmodel import Field, Session, SQLModel, select

from app.core.config import DEFAULT_UID
from app.core.identity import _uid
from app.db.engine import _now, get_session


def _uuid() -> str:
    return _uuidmod.uuid4().hex


def _dedupe_key(*args, **kwargs):
    """Lazy bridge to legacy._dedupe_key (helper used by _find_duplicate).

    Still lives in legacy.py; moves with the providers PR.
    """
    from app.legacy import _dedupe_key as _impl  # noqa: PLC0415
    return _impl(*args, **kwargs)


# ---------------------------------------------------------------------------
# === Extracted blocks (preserved verbatim from legacy.py) ===
# ---------------------------------------------------------------------------

class WorkMode(str, Enum):
    remote = "remote"
    hybrid = "hybrid"
    onsite = "onsite"
    any = "any"

class JobPosting(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(default=DEFAULT_UID, index=True)
    title: str
    company: str
    location: Optional[str] = None
    work_mode: WorkMode = WorkMode.any
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    source: str = "manual_import"
    source_url: Optional[str] = None
    description_text: str = ""
    requirements: List[str] = Field(default_factory=list,
                                    sa_column=Column(JSON))
    # Phase 2: must-have / nice-to-have / seniority / skills / domains.
    # The flat `requirements` list above is preserved unchanged for
    # backward compatibility; this is purely additive structure.
    structured_requirements: dict = Field(default_factory=dict,
                                           sa_column=Column(JSON))
    posted_at: Optional[str] = None
    imported_at: datetime = Field(default_factory=_now)
    is_archived: bool = False
    # Phase 5: provider tracking + freshness
    provider_source: Optional[str] = None
    provider_job_id: Optional[str] = None
    last_seen_at: Optional[datetime] = Field(default=None)
    is_stale: bool = False

_TITLE_HINT = re.compile(r"(?im)^\s*(?:title|role|position)\s*[:\-]\s*(.+)$")

_COMPANY_HINT = re.compile(
    r"(?im)^\s*(?:company|organization|employer)\s*[:\-]\s*(.+)$")

_LOCATION_HINT = re.compile(r"(?im)^\s*(?:location|based in)\s*[:\-]\s*(.+)$")

_SALARY = re.compile(
    r"\$\s?(\d[\d,]*)(?:\s?[kK])?\s?(?:-|to|\u2013|\u2014)\s?\$?\s?(\d[\d,]*)"
    r"(?:\s?[kK])?")

def _first(pattern, text):
    m = pattern.search(text)
    return m.group(1).strip() if m else None

def _money(raw, has_k):
    n = int(raw.replace(",", ""))
    return n * 1000 if has_k and n < 1000 else n

def _detect_work_mode(text):
    low = text.lower()
    if "hybrid" in low:
        return WorkMode.hybrid
    if "remote" in low:
        return WorkMode.remote
    if "on-site" in low or "onsite" in low or "in office" in low:
        return WorkMode.onsite
    return WorkMode.any

def import_job(description_text, source_url=None, title=None, company=None,
               source="manual_import"):
    # Lazy: _extract_requirements and _structured_requirements
    # are parsing helpers still in legacy.py (move in parsing PR).
    from app.legacy import (
        _extract_requirements, _structured_requirements,
    )
    text = description_text or ""
    title = title or _first(_TITLE_HINT, text) or "Untitled Role"
    company = company or _first(_COMPANY_HINT, text) or "Unknown Company"
    location = _first(_LOCATION_HINT, text)
    salary_min = salary_max = None
    sm = _SALARY.search(text)
    if sm:
        has_k = bool(re.search(r"\d\s?[kK]", sm.group(0)))
        salary_min = _money(sm.group(1), has_k)
        salary_max = _money(sm.group(2), has_k)
    return JobPosting(
        title=title.strip()[:160], company=company.strip()[:120],
        location=location, work_mode=_detect_work_mode(text),
        salary_min=salary_min, salary_max=salary_max,
        source=source, source_url=source_url,
        description_text=text, requirements=_extract_requirements(text),
        structured_requirements=_structured_requirements(text))

def _find_duplicate(session, company, title, source_url):
    key = _dedupe_key(company, title, source_url)
    for j in session.exec(select(JobPosting).where(
            JobPosting.owner_id == _uid())).all():
        if _dedupe_key(j.company, j.title, j.source_url) == key:
            return j
    return None

def _job_read(j, deduplicated=False):
    return JobRead(
        id=j.id, title=j.title, company=j.company, location=j.location,
        work_mode=j.work_mode, salary_min=j.salary_min,
        salary_max=j.salary_max, source=j.source, source_url=j.source_url,
        description_text=j.description_text, requirements=j.requirements,
        structured_requirements=j.structured_requirements or {},
        is_archived=j.is_archived, deduplicated=deduplicated,
        posted_at=j.posted_at, imported_at=j.imported_at,
        provider_source=getattr(j, 'provider_source', None),
        provider_job_id=getattr(j, 'provider_job_id', None),
        last_seen_at=getattr(j, 'last_seen_at', None),
        is_stale=bool(getattr(j, 'is_stale', False)))

class JobImportRequest(BaseModel):
    description_text: str = ""
    source_url: Optional[str] = None
    title: Optional[str] = None
    company: Optional[str] = None

class UrlImportRequest(BaseModel):
    url: str
    title: Optional[str] = None
    company: Optional[str] = None

class JobRead(BaseModel):
    id: str
    title: str
    company: str
    location: Optional[str]
    work_mode: WorkMode
    salary_min: Optional[int]
    salary_max: Optional[int]
    source: str
    source_url: Optional[str]
    description_text: str
    requirements: List[str]
    structured_requirements: dict = {}
    is_archived: bool = False
    deduplicated: bool = False
    posted_at: Optional[str]
    imported_at: datetime
    # Phase 5
    provider_source: Optional[str] = None
    provider_job_id: Optional[str] = None
    last_seen_at: Optional[datetime] = None
    is_stale: bool = False

jobs_router = APIRouter(prefix="/api/jobs", tags=["jobs"])

@jobs_router.get("", response_model=List[JobRead])
def list_jobs(session: Session = Depends(get_session)):
    rows = session.exec(select(JobPosting).where(
        JobPosting.owner_id == _uid(),
        JobPosting.is_archived == False).order_by(  # noqa: E712
        JobPosting.imported_at.desc())).all()
    return [_job_read(j) for j in rows]

@jobs_router.post("", response_model=JobRead, status_code=201)
def create_job(body: JobImportRequest, response: Response,
               session: Session = Depends(get_session)):
    if not body.description_text.strip():
        raise HTTPException(400, "description_text is required")
    job = import_job(body.description_text, body.source_url,
                     body.title, body.company)
    job.owner_id = _uid()
    dup = _find_duplicate(session, job.company, job.title, job.source_url)
    if dup is not None:
        response.status_code = 200
        return _job_read(dup, deduplicated=True)
    session.add(job)
    session.commit()
    session.refresh(job)
    return _job_read(job)

@jobs_router.post("/import-url", response_model=JobRead,
                  status_code=201)
def import_job_from_url(body: UrlImportRequest, response: Response,
                        session: Session = Depends(get_session)):
    """Fetch ONE user-supplied public posting URL (no crawling), reduce
    it to text, and import it through the same normaliser. Guardrails:
    scheme check, auth-walled-host denylist, robots.txt, timeout, size
    cap, HTML/text only. Failures surface as a clear 4xx."""
    # Lazy bridges to helpers still in legacy.py.
    # Reading from `app` at call time so test monkeypatches
    # like `monkeypatch.setattr(A, 'fetch_url_text', boom)`
    # propagate to this code path.
    from app import fetch_url_text, UrlFetchError  # noqa: PLC0415
    try:
        text = fetch_url_text(body.url)
    except UrlFetchError as e:
        raise HTTPException(422, str(e))
    job = import_job(text, source_url=body.url, title=body.title,
                     company=body.company, source="url_import")
    job.owner_id = _uid()
    dup = _find_duplicate(session, job.company, job.title, job.source_url)
    if dup is not None:
        response.status_code = 200
        return _job_read(dup, deduplicated=True)
    session.add(job)
    session.commit()
    session.refresh(job)
    return _job_read(job)
