"""Aptiro backend module: `app.modules.profile_truth`.

Phase 9 PR-8 — claim domain extracted from legacy.py:
  * Enums     — ClaimType, ApprovalStatus, ProvenanceCategory
  * Mapping   — PROVENANCE_COLOR
  * Model     — ProfileClaim
  * Schemas   — ClaimRead, ClaimUpdate
  * Helpers   — provenance_for_source, provenance_color,
                claim_provenance, claim_read
  * Router    — claims_router with 3 endpoints (list, get, patch)

Behavior is identical to pre-PR-8. legacy.py re-imports every moved
name so the test contract (`import app as A; A.ProfileClaim`,
`A.ApprovalStatus`, `A.claim_provenance`, `A.claims_router`, etc.)
is unchanged. extract_claims, _claim_type, parse_document and the
rest of the parsing pipeline stay in legacy until the parsing PR.

Cross-module dependencies
─────────────────────────
  SourceType                  — module-level import from app.legacy
                                (SourceType stays in legacy because the
                                 Source model that uses it is in
                                 modules.sources; SourceType is defined
                                 EARLY in legacy, before our import
                                 line, so partial-load is safe).
  Source, SourceRef           — string forward refs in ProfileClaim's
                                Relationship() declarations (SQLAlchemy
                                resolves them at mapper-init).
  SourceRef, SourceRefRead    — module-level import from
                                app.modules.sources (PR-7). Safe:
                                modules.sources loads BEFORE
                                modules.profile_truth.

APTIRO_PHASE9_PR8_PROFILE_TRUTH_MARKER
"""

import uuid as _uuidmod
from datetime import datetime
from enum import Enum
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Column, JSON
from sqlmodel import Field, Relationship, Session, SQLModel, select

from app.core.config import DEFAULT_UID
from app.core.identity import _uid
from app.db.engine import _now, get_session

# SourceType stays in legacy (used by Source, which is in
# modules.sources). It is defined EARLY in legacy.py, before the
# `from app.modules.profile_truth import ...` line we insert, so this
# module-level import is safe at partial-load time.
from app.legacy import SourceType  # noqa: F401

# SourceRef + SourceRefRead are used by claim_read() when building
# the ClaimRead DTO. modules.sources loaded BEFORE us, so direct
# import is safe.
from app.modules.sources import Source, SourceRef, SourceRefRead  # noqa: F401


def _uuid() -> str:
    return _uuidmod.uuid4().hex


# ---------------------------------------------------------------------------
# === Extracted blocks (preserved verbatim from legacy.py) ===
# ---------------------------------------------------------------------------

class ClaimType(str, Enum):
    achievement = "achievement"
    responsibility = "responsibility"
    skill = "skill"
    education = "education"
    summary = "summary"


class ApprovalStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    do_not_use = "do_not_use"


class ProvenanceCategory(str, Enum):
    grounded_resume_truth = "grounded_resume_truth"
    profile_derived = "profile_derived"
    public_context_supported = "public_context_supported"
    ai_suggested = "ai_suggested"
    unsupported = "unsupported"


PROVENANCE_COLOR = {
    ProvenanceCategory.grounded_resume_truth: "blue",
    ProvenanceCategory.profile_derived: "purple",
    ProvenanceCategory.public_context_supported: "green",
    ProvenanceCategory.ai_suggested: "orange",
    ProvenanceCategory.unsupported: "red",
}


class ProfileClaim(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(default=DEFAULT_UID, index=True)
    source_id: str = Field(foreign_key="source.id", index=True)
    claim_text: str
    claim_type: ClaimType = ClaimType.achievement
    company: Optional[str] = None
    role: Optional[str] = None
    date_range: Optional[str] = None
    skills: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    metrics: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    confidence: float = 0.0
    approval_status: ApprovalStatus = ApprovalStatus.pending
    user_note: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    source: Optional[Source] = Relationship(back_populates="claims")
    source_refs: List["SourceRef"] = Relationship(
        back_populates="claim",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"})


def provenance_for_source(source_type):
    if source_type == SourceType.resume:
        return ProvenanceCategory.grounded_resume_truth
    if source_type in (SourceType.linkedin, SourceType.profile_text):
        return ProvenanceCategory.profile_derived
    if source_type == SourceType.public_url:
        return ProvenanceCategory.public_context_supported
    return ProvenanceCategory.unsupported


def provenance_color(category):
    return PROVENANCE_COLOR[category]


def claim_provenance(session, claim):
    if claim.approval_status in (ApprovalStatus.rejected,
                                 ApprovalStatus.do_not_use):
        return ProvenanceCategory.unsupported
    src = session.get(Source, claim.source_id)
    if src is None:
        return ProvenanceCategory.unsupported
    return provenance_for_source(src.source_type)


def claim_read(session, c):
    """Build ClaimRead DTO for a ProfileClaim, with provenance + refs.

    Restored after Phase 9 PR-7 accidentally removed it while
    extracting modules/sources. Verbatim behavior to pre-PR-7.
    """
    cat = claim_provenance(session, c)
    refs = session.exec(
        select(SourceRef).where(SourceRef.claim_id == c.id)).all()
    return ClaimRead(
        id=c.id, source_id=c.source_id, claim_text=c.claim_text,
        claim_type=c.claim_type, company=c.company, role=c.role,
        date_range=c.date_range, skills=c.skills, metrics=c.metrics,
        confidence=c.confidence, approval_status=c.approval_status,
        user_note=c.user_note, provenance_category=cat,
        provenance_color=provenance_color(cat),
        source_refs=[SourceRefRead(
            id=r.id, source_id=r.source_id, source_type=r.source_type,
            section=r.section, snippet=r.snippet, page=r.page,
            confidence=r.confidence) for r in refs])


class ClaimRead(BaseModel):
    id: str
    source_id: str
    claim_text: str
    claim_type: ClaimType
    company: Optional[str]
    role: Optional[str]
    date_range: Optional[str]
    skills: List[str]
    metrics: List[str]
    confidence: float
    approval_status: ApprovalStatus
    user_note: Optional[str]
    provenance_category: ProvenanceCategory
    provenance_color: str
    source_refs: List[SourceRefRead]


class ClaimUpdate(BaseModel):
    claim_text: Optional[str] = None
    approval_status: Optional[ApprovalStatus] = None
    user_note: Optional[str] = None
    skills: Optional[List[str]] = None
    metrics: Optional[List[str]] = None


claims_router = APIRouter(prefix="/api/claims", tags=["claims"])


@claims_router.get("", response_model=List[ClaimRead])
def list_claims(source_id: Optional[str] = None,
                session: Session = Depends(get_session)):
    q = select(ProfileClaim).where(ProfileClaim.owner_id == _uid())
    if source_id:
        q = q.where(ProfileClaim.source_id == source_id)
    rows = session.exec(q.order_by(ProfileClaim.created_at)).all()
    return [claim_read(session, c) for c in rows]


@claims_router.get("/{claim_id}", response_model=ClaimRead)
def get_claim(claim_id: str, session: Session = Depends(get_session)):
    c = session.get(ProfileClaim, claim_id)
    if not c:
        raise HTTPException(404, "Claim not found")
    return claim_read(session, c)


@claims_router.patch("/{claim_id}", response_model=ClaimRead)
def update_claim(claim_id: str, body: ClaimUpdate,
                 session: Session = Depends(get_session)):
    c = session.get(ProfileClaim, claim_id)
    if not c:
        raise HTTPException(404, "Claim not found")
    if body.claim_text is not None:
        c.claim_text = body.claim_text
    if body.approval_status is not None:
        c.approval_status = body.approval_status
    if body.user_note is not None:
        c.user_note = body.user_note
    if body.skills is not None:
        c.skills = body.skills
    if body.metrics is not None:
        c.metrics = body.metrics
    c.updated_at = _now()
    session.add(c)
    session.commit()
    session.refresh(c)
    return claim_read(session, c)

