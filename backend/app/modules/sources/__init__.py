"""
Aptiro backend — modules/sources/__init__.py (Phase 9 PR-7).

Source model, SourceRef model, Pydantic schemas, _source_read helper,
and sources_router with four endpoints (list, create, upload, delete).

Extracted from backend/app/legacy.py.

Cross-module deps (see phase9_pr7_sources.py docstring for detail):
  SourceType     — module-level import from app.legacy (safe, defined early)
  ProfileClaim   — string forward refs in SQLModel; lazy fn-body imports
  extract_claims — lazy fn-body imports until PR-8
  ingestion      — standalone module, no circular risk
"""
import uuid as _uuidmod
from datetime import datetime
from typing import List, Optional

import ingestion
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import Column, JSON
from sqlmodel import Field, Relationship, Session, SQLModel, select

from app.core.config import DEFAULT_UID
from app.core.identity import _uid
from app.db.engine import _now, get_session

# SourceType is an enum defined early in legacy.py — safe to import at
# module level because it is already in legacy's namespace when Python
# starts loading this file (circular-import partial-module mechanism).
from app.legacy import SourceType  # noqa: F401


def _uuid() -> str:
    return _uuidmod.uuid4().hex


# ─── Models ──────────────────────────────────────────────────────────────────

class Source(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(default=DEFAULT_UID, index=True)
    source_type: SourceType
    label: str
    filename: Optional[str] = None
    raw_text: str = ""
    extracted_text: str = ""
    parse_meta: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)
    # String forward ref — ProfileClaim lives in legacy.py until PR-8.
    # SQLAlchemy resolves it lazily after all models are registered.
    claims: List["ProfileClaim"] = Relationship(back_populates="source")


class SourceRef(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    claim_id: str = Field(foreign_key="profileclaim.id", index=True)
    source_id: str = Field(foreign_key="source.id", index=True)
    source_type: SourceType
    section: str = ""
    snippet: str = ""
    page: Optional[int] = None
    confidence: float = 0.0
    # String forward ref for the same reason as above.
    claim: Optional["ProfileClaim"] = Relationship(
        back_populates="source_refs")


# ─── Pydantic schemas ─────────────────────────────────────────────────────────

class SourceCreate(BaseModel):
    source_type: SourceType
    label: str
    raw_text: str = ""
    filename: Optional[str] = None


class SourceRead(BaseModel):
    id: str
    source_type: SourceType
    label: str
    filename: Optional[str]
    extracted_text: str
    parse_meta: dict = {}
    created_at: datetime
    claim_count: int = 0


class SourceRefRead(BaseModel):
    id: str
    source_id: str
    source_type: SourceType
    section: str
    snippet: str
    page: Optional[int] = None
    confidence: float


# ─── Helper ──────────────────────────────────────────────────────────────────

def _source_read(session, s):
    # Lazy: ProfileClaim moves to modules/profile_truth in PR-8.
    from app.legacy import ProfileClaim  # noqa: PLC0415
    cnt = len(session.exec(
        select(ProfileClaim).where(ProfileClaim.source_id == s.id)).all())
    return SourceRead(
        id=s.id, source_type=s.source_type, label=s.label,
        filename=s.filename, extracted_text=s.extracted_text,
        parse_meta=s.parse_meta or {}, created_at=s.created_at,
        claim_count=cnt)


# ─── Router ──────────────────────────────────────────────────────────────────

sources_router = APIRouter(prefix="/api/sources", tags=["sources"])


@sources_router.get("", response_model=List[SourceRead])
def list_sources(session: Session = Depends(get_session)):
    rows = session.exec(
        select(Source).where(Source.owner_id == _uid())
        .order_by(Source.created_at.desc())).all()
    return [_source_read(session, s) for s in rows]


@sources_router.post("", response_model=SourceRead, status_code=201)
def create_source(body: SourceCreate,
                   session: Session = Depends(get_session)):
    # Lazy: extract_claims moves to modules/profile_truth in PR-8.
    from app.legacy import extract_claims  # noqa: PLC0415
    src = Source(source_type=body.source_type, label=body.label,
                 filename=body.filename, raw_text=body.raw_text,
                 extracted_text=body.raw_text, owner_id=_uid(),
                 parse_meta={"format": "text", "chars": len(body.raw_text)})
    session.add(src)
    session.commit()
    session.refresh(src)
    extract_claims(session, src)
    return _source_read(session, src)


@sources_router.post("/upload", response_model=SourceRead,
                     status_code=201)
async def upload_source(
        file: UploadFile = File(...),
        source_type: SourceType = Query(SourceType.resume),
        session: Session = Depends(get_session)):
    """Production ingestion: PDF / DOCX / TXT / Markdown.

    Real extraction lives in ingestion.py; provenance, snippets,
    sections, confidence and the approval gate are unchanged because the
    extracted text flows through the SAME parse_document/extract_claims
    pipeline.
    """
    from app.legacy import extract_claims  # noqa: PLC0415
    data = await file.read()
    if len(data) > ingestion.MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, "File exceeds the %d MB upload limit."
            % (ingestion.MAX_UPLOAD_BYTES // (1024 * 1024)))
    try:
        result = ingestion.extract(file.filename or "upload", data)
    except ingestion.UnsupportedFormat as e:
        raise HTTPException(415, str(e))
    except ingestion.ExtractionError as e:
        raise HTTPException(422, str(e))
    if not result.text.strip():
        raise HTTPException(
            422, "No extractable text found (the file may be a scanned "
                 "image; OCR is out of scope for this slice).")
    src = Source(
        source_type=source_type, label=file.filename or "Uploaded",
        filename=file.filename, raw_text=result.text, owner_id=_uid(),
        extracted_text=result.text, parse_meta=result.meta)
    session.add(src)
    session.commit()
    session.refresh(src)
    extract_claims(session, src)
    return _source_read(session, src)


@sources_router.delete("/{source_id}", status_code=204)
def delete_source(source_id: str,
                  session: Session = Depends(get_session)):
    from app.legacy import ProfileClaim  # noqa: PLC0415
    src = session.get(Source, source_id)
    if not src:
        raise HTTPException(404, "Source not found")
    for c in session.exec(select(ProfileClaim).where(
            ProfileClaim.source_id == source_id)).all():
        for r in session.exec(select(SourceRef).where(
                SourceRef.claim_id == c.id)).all():
            session.delete(r)
        session.delete(c)
    session.delete(src)
    session.commit()
