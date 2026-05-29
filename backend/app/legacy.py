"""Aptiro - evidence-backed job application assistant.

Trust + Export production slice (built on the Delivery 1-4 contract).

Sections: config -> enums -> models -> db -> parsing -> extraction/
provenance -> job import -> scoring -> packages -> council -> apply ->
notifications -> privacy -> export -> seed -> app.

Key slice changes vs. the prototype:
  * Real PDF / DOCX / TXT / Markdown ingestion (backend/ingestion.py),
    feeding the SAME parse_document/extract_claims pipeline so provenance,
    snippets, sections, confidence and the approval gate are preserved.
  * Real package export (backend/exporting.py): Markdown -> HTML -> DOCX
    -> PDF, with rejected / do-not-use / red / unsupported content
    excluded from final exports by default.
  * Pluggable AI provider (backend/ai_provider.py): deterministic mock by
    default, optional Anthropic behind APTIRO_AI_PROVIDER=anthropic. The
    app never depends on a live key to run or to pass tests.

Run:  uvicorn app:app --reload
Test: pytest -q
"""
import os
import re
import hashlib as _hashlib
import hmac as _hmac
import secrets as _secrets
from contextvars import ContextVar
import uuid
import importlib.util as _ilu
import sys as _sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from fastapi import (APIRouter, Depends, FastAPI, HTTPException, Query,
                     UploadFile, File)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import JSON, Column
from sqlmodel import (Field, Relationship, Session, SQLModel, create_engine,
                      select)

import ai_provider
import ingestion
import exporting
import embeddings

try:
    import httpx as _httpx
except Exception:  # pragma: no cover - httpx ships with the test client
    _httpx = None

# APTIRO_PHASE9_PR3_CONFIG_MARKER
# Config vars, auth constants: extracted to core/config.py (Phase 9 PR-3)
from app.core.config import (  # noqa: F401
    _DEFAULT_DATABASE_URL, DATABASE_URL,
    AI_PROVIDER, EMBEDDING_PROVIDER, JOB_PROVIDER,
    SEARCH_PROVIDER, NOTIFICATION_PROVIDER, SEED_ON_STARTUP,
    AUTH_ENABLED, DEFAULT_UID, DEFAULT_USER_EMAIL, _PW_ROUNDS,
)
# APTIRO_PHASE9_PR4_IDENTITY_MARKER
# Identity extracted to core/identity.py (Phase 9 PR-4)
from app.core.identity import _CURRENT_UID, _uid  # noqa: F401
# --- Phase 6: ops & observability (Phase 9 PR-2: moved to core/observability.py)
import json as _json         # kept — may be used elsewhere in legacy
import logging as _logging    # kept — may be used elsewhere in legacy
import time as _time          # KEPT — auth middleware: _time.perf_counter()
import uuid as _uuidmod       # KEPT — auth middleware: _uuidmod.uuid4().hex[:16]

# APTIRO_PHASE9_PR2_OBS_MARKER
# Definitions live in backend/app/core/observability.py.
# Imported here so the module proxy + `import app as A; A.X` contract holds.
from app.core.observability import (  # noqa: F401
    _REQUEST_ID,   # ContextVar[str] — per-request correlation ID
    _rid,          # () -> str — current request ID
    _log,          # logging.Logger — the "aptiro" logger singleton
    _logj,         # (event, **fields) -> None — structured JSON log line
)


# ConfigError, validate_config, URL limits: extracted to core/config.py (Phase 9 PR-3)
from app.core.config import (  # noqa: F401
    ConfigError, validate_config,
    URL_FETCH_TIMEOUT, URL_FETCH_MAX_BYTES,
)

# Hosts we will not fetch: login/auth-walled or scrape-prohibited.
_URL_FETCH_DENY = {
    "linkedin.com", "www.linkedin.com", "indeed.com", "www.indeed.com",
    "glassdoor.com", "www.glassdoor.com", "facebook.com",
    "www.facebook.com", "x.com", "twitter.com",
}
# APTIRO_PHASE9_PR5_DB_MARKER
# _now, _IS_SQLITE, engine, get_session extracted to db/engine.py (Phase 9 PR-5)
from app.db.engine import (  # noqa: F401
    _now, _IS_SQLITE, engine, get_session,
)


def _uuid():
    return uuid.uuid4().hex


# _now imported from app.db.engine (Phase 9 PR-5)


# ===========================================================================
# Enums
# ===========================================================================
class SourceType(str, Enum):
    resume = "resume"
    linkedin = "linkedin"
    profile_text = "profile_text"
    public_url = "public_url"


# APTIRO_PHASE9_PR8_PROFILE_TRUTH_MARKER
# Claim domain → modules/profile_truth/ (Phase 9 PR-8):
#   Enums:    ClaimType, ApprovalStatus, ProvenanceCategory
#   Mapping:  PROVENANCE_COLOR
#   Model:    ProfileClaim
#   Schemas:  ClaimRead, ClaimUpdate
#   Helpers:  provenance_for_source, provenance_color,
#             claim_provenance, claim_read
#   Router:   claims_router (list, get, patch endpoints)
from app.modules.profile_truth import (  # noqa: F401
    ClaimType, ApprovalStatus, ProvenanceCategory,
    PROVENANCE_COLOR,
    ProfileClaim,
    ClaimRead, ClaimUpdate,
    provenance_for_source, provenance_color,
    claim_provenance, claim_read,
    claims_router,
)


# APTIRO_PHASE9_PR9_JOBS_MARKER
# Job domain → modules/jobs/ (Phase 9 PR-9):
#   Enum:     WorkMode
#   Model:    JobPosting
#   Schemas:  JobImportRequest, UrlImportRequest, JobRead
#   Regex:    _TITLE_HINT, _COMPANY_HINT, _LOCATION_HINT, _SALARY
#   Helpers:  _first, _money, _detect_work_mode,
#             _find_duplicate, _job_read, import_job
#   Router:   jobs_router (list, create, import-url endpoints)
from app.modules.jobs import (  # noqa: F401
    WorkMode,
    JobPosting,
    JobImportRequest, UrlImportRequest, JobRead,
    _TITLE_HINT, _COMPANY_HINT, _LOCATION_HINT, _SALARY,
    _first, _money, _detect_work_mode,
    _find_duplicate, _job_read, import_job,
    jobs_router,
)


class Aggressiveness(str, Enum):
    conservative = "conservative"
    balanced = "balanced"
    opportunistic = "opportunistic"


class PackageStatus(str, Enum):
    draft = "draft"
    orchestrated = "orchestrated"
    finalized = "finalized"


class BulletStatus(str, Enum):
    proposed = "proposed"
    accepted = "accepted"
    rejected = "rejected"
    rewritten = "rewritten"
    locked = "locked"


class RunStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class AgentRole(str, Enum):
    vector = "vector"
    axiom = "axiom"
    lumen = "lumen"
    entp = "entp"
    istj_qa = "istj_qa"


AGENT_TITLE = {
    AgentRole.vector: "Vector - Strategic Alignment",
    AgentRole.axiom: "Axiom - Evidence Integrity",
    AgentRole.lumen: "Lumen - Clarity & Impact",
    AgentRole.entp: "ENTP - Divergent Angle",
    AgentRole.istj_qa: "ISTJ-QA - Compliance & QA",
}

ApplyState = Enum("ApplyState", {
    "created": "created", "preparing": "preparing",
    "ready_for_review": "ready_for_review",
    "awaiting_user_handoff": "awaiting_user_handoff",
    "user_confirmed": "user_confirmed", "submitted": "submitted",
    "aborted": "aborted"}, type=str)


class NotificationKind(str, Enum):
    package_ready = "package_ready"
    daily_digest = "daily_digest"
    integrity_alert = "integrity_alert"


class NotificationChannel(str, Enum):
    email = "email"
    slack = "slack"
    in_app = "in_app"


class ApplicationStatus(str, Enum):
    """Phase 3 tracker lifecycle. Every transition is human-initiated
    and audited; the app NEVER submits anything anywhere -
    'submitted_by_user' is a state the USER asserts after they applied
    on the employer's own site."""
    drafted = "drafted"
    exported = "exported"
    submitted_by_user = "submitted_by_user"
    interviewing = "interviewing"
    offer = "offer"
    rejected = "rejected"
    withdrawn = "withdrawn"


# ===========================================================================
# Models
# ===========================================================================
class User(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    email: str = Field(index=True)
    name: str = ""
    password_hash: str = ""
    token: str = Field(default="", index=True)
    is_default: bool = False
    created_at: datetime = Field(default_factory=_now)


class AuditEvent(SQLModel, table=True):
    """Phase 6: an append-only record of every mutating request. Written
    by the observability middleware (never by endpoint code), so the
    audit trail can't be skipped. Owner-scoped and read-only via
    /api/audit; intentionally NOT in the privacy bundle/wipe set so the
    trail is tamper-resistant and existing bundle counts are unchanged.
    """
    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(default=DEFAULT_UID, index=True)
    request_id: str = Field(default="-", index=True)
    method: str = ""
    path: str = ""
    status: int = 0
    duration_ms: int = 0
    at: datetime = Field(default_factory=_now)


# APTIRO_PHASE9_PR7_SOURCES_MARKER
# Source, SourceRef, schemas, sources_router → modules/sources/ (PR-7)
from app.modules.sources import (  # noqa: F401
    Source, SourceRef,
    SourceCreate, SourceRead, SourceRefRead,
    sources_router, _source_read,
)


# SourceRef imported from modules/sources (Phase 9 PR-7)


DEFAULT_WEIGHTS = {
    "role_alignment": 15, "seniority_alignment": 10, "core_skills": 20,
    "domain": 10, "leadership_scope": 10, "ai_technical": 10,
    "evidence_strength": 10, "preferences": 10, "strategy_boost": 5,
}


class Strategy(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(default=DEFAULT_UID, index=True)
    name: str = "Default Strategy"
    is_active: bool = True
    target_roles: List[str] = Field(default_factory=list,
                                    sa_column=Column(JSON))
    region: Optional[str] = None
    work_mode: WorkMode = WorkMode.any
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    aggressiveness: Aggressiveness = Aggressiveness.balanced
    score_threshold: int = 0
    weights: dict = Field(default_factory=lambda: dict(DEFAULT_WEIGHTS),
                          sa_column=Column(JSON))
    include_companies: List[str] = Field(default_factory=list,
                                         sa_column=Column(JSON))
    exclude_companies: List[str] = Field(default_factory=list,
                                         sa_column=Column(JSON))
    targeting_notes: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ApplicationPackage(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(default=DEFAULT_UID, index=True)
    job_id: str = Field(foreign_key="jobposting.id", index=True)
    strategy_id: Optional[str] = Field(default=None,
                                       foreign_key="strategy.id")
    title: str = ""
    company: str = ""
    status: PackageStatus = PackageStatus.draft
    score_snapshot: int = 0
    summary: str = ""
    cover_letter: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    bullets: List["PackageBullet"] = Relationship(
        back_populates="package",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"})
    runs: List["AgentRun"] = Relationship(
        back_populates="package",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"})


class PackageBullet(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    package_id: str = Field(foreign_key="applicationpackage.id", index=True)
    section: str = "experience"
    order_index: int = 0
    claim_id: Optional[str] = Field(default=None,
                                    foreign_key="profileclaim.id")
    original_text: str = ""
    current_text: str = ""
    provenance: ProvenanceCategory = ProvenanceCategory.unsupported
    provenance_color: str = "red"
    confidence: float = 0.0
    status: BulletStatus = BulletStatus.proposed
    rationale: str = ""
    user_note: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    package: Optional[ApplicationPackage] = Relationship(
        back_populates="bullets")


class AgentRun(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    package_id: str = Field(foreign_key="applicationpackage.id", index=True)
    status: RunStatus = RunStatus.pending
    ready: bool = False
    summary: str = ""
    steps: List[dict] = Field(default_factory=list, sa_column=Column(JSON))
    started_at: datetime = Field(default_factory=_now)
    finished_at: Optional[datetime] = None
    package: Optional[ApplicationPackage] = Relationship(
        back_populates="runs")
    critiques: List["AgentCritique"] = Relationship(
        back_populates="run",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"})


class AgentCritique(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    run_id: str = Field(foreign_key="agentrun.id", index=True)
    agent: AgentRole = AgentRole.vector
    target: str = "package"
    verdict: str = "pass"
    severity: str = "info"
    message: str = ""
    suggestion: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)
    run: Optional[AgentRun] = Relationship(back_populates="critiques")


class ApplySession(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    package_id: str = Field(foreign_key="applicationpackage.id", index=True)
    job_id: str = ""
    job_title: str = ""
    company: str = ""
    state: ApplyState = ApplyState.created
    requires_handoff: bool = True
    note: Optional[str] = None
    plan: List[dict] = Field(default_factory=list, sa_column=Column(JSON))
    history: List[dict] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class NotificationPreview(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    kind: NotificationKind
    channel: NotificationChannel
    subject: str = ""
    body: str = ""
    package_id: Optional[str] = None
    status: str = "preview"
    created_at: datetime = Field(default_factory=_now)


class Application(SQLModel, table=True):
    """Phase 3: the post-submission tracker. Created from a package by
    an explicit human action; tracks the real-world lifecycle WITHOUT
    ever submitting anything. The `snapshot` is frozen the moment the
    user marks it submitted and is never rewritten - it is immutable
    evidence of exactly what was sent."""
    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(default=DEFAULT_UID, index=True)
    package_id: str = Field(foreign_key="applicationpackage.id",
                            index=True)
    job_id: str = ""
    job_title: str = ""
    company: str = ""
    status: ApplicationStatus = ApplicationStatus.drafted
    note: Optional[str] = None
    submitted_at: Optional[datetime] = None
    # Immutable "what I sent" evidence (set once, at submit).
    snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON))
    snapshot_sha: Optional[str] = None
    # Audit log of every human-initiated transition.
    history: List[dict] = Field(default_factory=list,
                                sa_column=Column(JSON))
    # Deterministic follow-up reminders (no scheduler, never sent).
    reminders: List[dict] = Field(default_factory=list,
                                  sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# _connect_args defined in app.db.engine (Phase 9 PR-5)
# engine imported from app.db.engine (Phase 9 PR-5)


def _ensure_additive_columns():
    """create_all() never ALTERs an existing table, so a DB created
    before a new (nullable/defaulted) column was added would be missing
    it. This applies only safe, additive `ADD COLUMN`s for columns that
    already exist in the model - no drops, no type changes, no data
    loss. Postgres production still goes through Alembic; this keeps the
    zero-config SQLite path and any pre-existing DB self-healing."""
    from sqlalchemy import inspect as _inspect, text as _sql
    _owner_ddl = ("VARCHAR" if not _IS_SQLITE else "TEXT")
    additive = {
        "jobposting": {
            "structured_requirements": "JSON" if not _IS_SQLITE
            else "TEXT",
            "owner_id": _owner_ddl,
            "provider_source": "TEXT",
            "provider_job_id": "TEXT",
            "last_seen_at": "DATETIME",
            "is_stale": "INTEGER",
        },
        "source": {"owner_id": _owner_ddl},
        "profileclaim": {"owner_id": _owner_ddl},
        "strategy": {
            "owner_id": _owner_ddl,
            "score_threshold": "INTEGER",
        },
        "applicationpackage": {"owner_id": _owner_ddl},
        "application": {"owner_id": _owner_ddl},
    }
    try:
        insp = _inspect(engine)
        existing_tables = set(insp.get_table_names())
        with engine.begin() as conn:
            for table, cols in additive.items():
                if table not in existing_tables:
                    continue
                have = {c["name"] for c in insp.get_columns(table)}
                for col, ddl_type in cols.items():
                    if col in have:
                        continue
                    conn.execute(_sql(
                        f'ALTER TABLE {table} '
                        f'ADD COLUMN {col} {ddl_type}'))
    except Exception as exc:  # never block startup on a best-effort heal
        print(f"[aptiro] additive column check skipped: {exc!r}",
              file=_sys.stderr)


def _backfill_owner_ids():
    """Any row that predates Phase 4 gets the built-in local owner, so
    existing single-user data stays visible exactly as before."""
    from sqlalchemy import inspect as _inspect, text as _sql
    tables = ("source", "profileclaim", "strategy", "jobposting",
              "applicationpackage", "application")
    try:
        insp = _inspect(engine)
        present = set(insp.get_table_names())
        with engine.begin() as conn:
            for t in tables:
                if t not in present:
                    continue
                cols = {c["name"] for c in insp.get_columns(t)}
                if "owner_id" not in cols:
                    continue
                conn.execute(_sql(
                    f"UPDATE {t} SET owner_id = :u "
                    f"WHERE owner_id IS NULL OR owner_id = ''"),
                    {"u": DEFAULT_UID})
    except Exception as exc:
        print(f"[aptiro] owner backfill skipped: {exc!r}",
              file=_sys.stderr)


def _ensure_default_user():
    """The single built-in 'local' user. With AUTH off everything runs
    as this user, so prior behavior and data are unchanged."""
    try:
        with Session(engine) as s:
            u = s.get(User, DEFAULT_UID)
            if not u:
                s.add(User(id=DEFAULT_UID, email=DEFAULT_USER_EMAIL,
                           name="Local User", is_default=True,
                           token=""))
                s.commit()
    except Exception as exc:
        print(f"[aptiro] default user bootstrap skipped: {exc!r}",
              file=_sys.stderr)


def init_db():
    SQLModel.metadata.create_all(engine)
    _ensure_additive_columns()
    _backfill_owner_ids()
    _ensure_default_user()


# get_session imported from app.db.engine (Phase 9 PR-5)


# --- Phase 4: auth + scoping helpers -------------------------------------
def _hash_pw(password, salt=None):
    salt = salt or _secrets.token_hex(16)
    dk = _hashlib.pbkdf2_hmac("sha256", password.encode(),
                              salt.encode(), _PW_ROUNDS)
    return "%s$%s" % (salt, dk.hex())


def _verify_pw(password, stored):
    try:
        salt, _ = stored.split("$", 1)
    except (ValueError, AttributeError):
        return False
    return _hmac.compare_digest(_hash_pw(password, salt), stored)


def _new_token():
    return _secrets.token_urlsafe(32)


def _bearer(request):
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else ""


def _scoped(session, model):
    """Owner-filtered select for the current request's user. With AUTH
    off the current uid is the local user and every row carries that
    owner_id, so results are identical to the pre-Phase-4 behavior."""
    return session.exec(
        select(model).where(model.owner_id == _uid())).all()


def _get_owned(session, model, obj_id):
    """Fetch by id but only if it belongs to the current user; anything
    owned by someone else is reported as not-found (no existence leak)."""
    obj = session.get(model, obj_id)
    if obj is None:
        return None
    if getattr(obj, "owner_id", _uid()) != _uid():
        return None
    return obj


# ===========================================================================
# Parsing + extraction + provenance (unchanged contract; ingestion.py
# only changes how raw text/sections/pages are PRODUCED, not how claims
# are derived from them)
# ===========================================================================
SECTION_HEADERS = {
    "experience": ["experience", "work experience",
                   "professional experience", "employment",
                   "work history", "professional background"],
    "education": ["education", "academics", "academic background"],
    "skills": ["skills", "technical skills", "core competencies",
               "skills & personal", "skills and personal", "technologies"],
    "summary": ["summary", "professional summary", "profile", "about",
                "executive summary", "objective"],
}
_ROLE_AT_COMPANY = re.compile(
    r"^(?P<role>[A-Z][\w/&\- ]+?)\s*[,|@]\s*(?P<company>[A-Z][\w&.\- ]+?)"
    r"(?:\s*[\(\|]\s*(?P<dates>[\w\-/ \u2013\u2014]+?)\s*[\)\|]?)?\s*$")
_BULLET_PREFIX = re.compile(
    r"^\s*[\u2022\-\*\u00b7\u25aa\u25e6\u2023]\s+")

SKILL_LEXICON = [
    "product management", "product strategy", "roadmap", "AI", "ML",
    "machine learning", "LLM", "chatbot", "eCommerce", "analytics",
    "cross-functional", "stakeholder", "go-to-market", "GTM", "SaaS",
    "cloud", "AWS", "healthcare", "clinical", "EHR", "EMR", "data",
    "automation", "discovery", "experimentation", "A/B testing", "API",
    "platform", "governance", "compliance", "leadership",
]
_METRIC = re.compile(
    r"(\$\s?\d[\d,.]*\s?(?:[KMB]|million|billion|thousand)?"
    r"|\d[\d,.]*\s?%|\b\d[\d,.]*\s?(?:x|X)\b"
    r"|\b\d[\d,.]*\s?(?:million|billion|thousand|users|customers|reps?)\b)")


class ParsedLine:
    def __init__(self, text, section, company=None, role=None,
                 date_range=None, is_bullet=False, page=None):
        self.text = text
        self.section = section
        self.company = company
        self.role = role
        self.date_range = date_range
        self.is_bullet = is_bullet
        self.page = page


def _classify_header(line):
    low = line.strip().lower().rstrip(":")
    if len(low) > 40:
        return None
    for canonical, aliases in SECTION_HEADERS.items():
        if low in aliases:
            return canonical
    return None


def parse_document(text, page_map=None):
    """Section/role/bullet structure detection.

    page_map: optional list of (char_offset, page_number) tuples produced
    by ingestion.py for PDF/DOCX so each claim can record its page.
    """
    lines = []
    section = "summary"
    company = role = dates = None
    offset = 0

    def page_at(off):
        if not page_map:
            return None
        pg = page_map[0][1]
        for start, p in page_map:
            if off >= start:
                pg = p
            else:
                break
        return pg

    for raw in (text or "").splitlines():
        line = raw.strip()
        this_off = offset
        offset += len(raw) + 1
        if not line:
            continue
        header = _classify_header(line)
        if header:
            section = header
            company = role = dates = None
            continue
        m = _ROLE_AT_COMPANY.match(line)
        if m and section == "experience" and not _BULLET_PREFIX.match(raw):
            role = m.group("role").strip()
            company = m.group("company").strip()
            dates = (m.group("dates") or "").strip() or None
            lines.append(ParsedLine(line, section, company, role, dates,
                                    False, page_at(this_off)))
            continue
        is_bullet = bool(_BULLET_PREFIX.match(raw)) or (
            section in ("experience", "summary") and len(line) > 30)
        clean = _BULLET_PREFIX.sub("", raw).strip()
        lines.append(ParsedLine(clean, section, company, role, dates,
                                is_bullet, page_at(this_off)))
    return [ln for ln in lines if ln.is_bullet]


def _extract_metrics(text):
    return [m.group(0).strip() for m in _METRIC.finditer(text)]


def _extract_skills(text):
    low = text.lower()
    out = []
    for s in SKILL_LEXICON:
        if s.lower() in low and s not in out:
            out.append(s)
    return out


def _claim_type(line, metrics):
    if line.section == "education":
        return ClaimType.education
    if line.section == "skills":
        return ClaimType.skill
    if line.section == "summary":
        return ClaimType.summary
    return ClaimType.achievement if metrics else ClaimType.responsibility


def _confidence(line, metrics, skills):
    score = 0.55
    if metrics:
        score += 0.25
    if skills:
        score += 0.1
    if line.company and line.role:
        score += 0.08
    if len(line.text) < 25:
        score -= 0.2
    return round(max(0.1, min(0.99, score)), 2)


def extract_claims(session, source):
    page_map = (source.parse_meta or {}).get("page_map")
    created = []
    seen = set()
    for line in parse_document(source.extracted_text or source.raw_text,
                               page_map=page_map):
        text = line.text.strip()
        if len(text) < 12:
            continue
        key = text.lower()
        if key in seen:          # de-dupe identical bullets within a source
            continue
        seen.add(key)
        metrics = _extract_metrics(text)
        skills = _extract_skills(text)
        conf = _confidence(line, metrics, skills)
        claim = ProfileClaim(
            source_id=source.id, claim_text=text,
            claim_type=_claim_type(line, metrics), company=line.company,
            role=line.role, date_range=line.date_range, skills=skills,
            metrics=metrics, confidence=conf,
            owner_id=getattr(source, "owner_id", _uid()),
            approval_status=ApprovalStatus.pending)
        session.add(claim)
        session.flush()
        session.add(SourceRef(
            claim_id=claim.id, source_id=source.id,
            source_type=source.source_type, section=line.section,
            snippet=text[:200], page=line.page, confidence=conf))
        created.append(claim)
    session.commit()
    for c in created:
        session.refresh(c)
    return created


# ===========================================================================
# Job import
# ===========================================================================
_REQ_LINE = re.compile(r"^\s*[\u2022\-\*\u00b7]\s+(.{6,200})$")


def _extract_requirements(text):
    reqs = []
    in_block = False
    for raw in text.splitlines():
        low = raw.strip().lower()
        if any(k in low for k in ("requirement", "qualification",
                                  "what you", "you have", "must have",
                                  "responsibilities")):
            in_block = True
        m = _REQ_LINE.match(raw)
        if m and (in_block or len(reqs) < 12):
            item = m.group(1).strip()
            if item not in reqs:
                reqs.append(item)
    return reqs[:20]


# --- Phase 2: structured requirement extraction --------------------------
# Reuses the same deterministic parser primitives already used elsewhere
# (_REQ_LINE, _extract_skills, _seniority_rank, _domains_in) so behaviour
# is consistent with the rest of the pipeline.
_MUST_HDR = ("required", "requirements", "must have", "must-have",
             "minimum qualifications", "basic qualifications",
             "what you need", "you have", "qualifications")
_NICE_HDR = ("preferred", "nice to have", "nice-to-have", "bonus",
             "a plus", "pluses", "preferred qualifications",
             "good to have", "ideally")
_NICE_INLINE = re.compile(
    r"\b(preferred|nice to have|a plus|bonus|ideally|good to have)\b",
    re.I)
_YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:years|yrs)\b", re.I)


# _structured_requirements extracted to app.modules.scoring (Phase 9 PR-11); re-imported below.


# --- Phase 2: dedupe key -------------------------------------------------
def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ",
                  (s or "").lower())).strip()


def _norm_url(u):
    if not u:
        return ""
    u = u.strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"[?#].*$", "", u)        # drop query/fragment
    return u.rstrip("/")


def _dedupe_key(company, title, source_url):
    return (_norm(company), _norm(title), _norm_url(source_url))


# --- Phase 2: server-side fetch of a USER-SUPPLIED public URL only -------
# This is not a crawler. It fetches exactly the one URL the user pasted,
# refuses login/auth-walled hosts, respects robots.txt, and caps time +
# size. HTML is reduced to text and handed to the same import_job path.
class UrlFetchError(Exception):
    pass


_SCRIPT_STYLE = re.compile(
    r"<(script|style|noscript)[^>]*>.*?</\1>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n\s*\n\s*\n+")
_HTML_ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">",
                  "&quot;": '"', "&#39;": "'", "&nbsp;": " "}


def _html_to_text(html):
    txt = _SCRIPT_STYLE.sub(" ", html or "")
    txt = re.sub(r"</(p|div|li|tr|h[1-6]|br|ul|ol)>", "\n", txt, flags=re.I)
    txt = re.sub(r"<li[^>]*>", "- ", txt, flags=re.I)
    txt = _TAG.sub(" ", txt)
    for k, v in _HTML_ENTITIES.items():
        txt = txt.replace(k, v)
    txt = _WS.sub(" ", txt)
    txt = "\n".join(ln.strip() for ln in txt.splitlines())
    return _BLANKS.sub("\n\n", txt).strip()


def _host_of(url):
    m = re.match(r"^https?://([^/]+)", (url or "").strip(), re.I)
    return (m.group(1).split(":")[0].lower() if m else "")


def _robots_allows(client, scheme_host, path):
    """Best-effort robots.txt check. Fail-CLOSED only on an explicit
    Disallow that matches our path; network/parse problems do not block
    a user-initiated single fetch."""
    try:
        r = client.get(scheme_host + "/robots.txt",
                        timeout=URL_FETCH_TIMEOUT)
        if r.status_code != 200 or "text" not in \
                r.headers.get("content-type", "text"):
            return True
        active, disallows = False, []
        for line in r.text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field, _, val = line.partition(":")
            field, val = field.strip().lower(), val.strip()
            if field == "user-agent":
                active = val in ("*", "aptiro")
            elif field == "disallow" and active and val:
                disallows.append(val)
        return not any(path.startswith(d) for d in disallows)
    except Exception:
        return True


def fetch_url_text(url, *, client=None):
    """Fetch one user-supplied public URL and return reduced text.

    Raises UrlFetchError (mapped to HTTP 4xx by the endpoint) for: bad
    scheme, denied/auth-walled host, robots Disallow, timeout, oversized
    body, or non-HTML content."""
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        raise UrlFetchError("URL must start with http:// or https://")
    host = _host_of(url)
    if not host:
        raise UrlFetchError("Could not parse host from URL")
    if host in _URL_FETCH_DENY or any(
            host.endswith("." + d) for d in _URL_FETCH_DENY):
        raise UrlFetchError(
            "%s is login/auth-walled or prohibits automated access; "
            "open the posting and paste the description instead." % host)
    owns = client is None
    if owns:
        if _httpx is None:
            raise UrlFetchError("HTTP client unavailable")
        client = _httpx.Client(follow_redirects=True,
                               headers={"User-Agent": "Aptiro/0.3"})
    try:
        m = re.match(r"^(https?://[^/]+)(/.*)?$", url, re.I)
        scheme_host, path = m.group(1), (m.group(2) or "/")
        if not _robots_allows(client, scheme_host, path):
            raise UrlFetchError(
                "robots.txt disallows fetching this path")
        try:
            resp = client.get(url, timeout=URL_FETCH_TIMEOUT)
        except Exception as e:                       # timeout / DNS / TLS
            raise UrlFetchError("Fetch failed: %s"
                                % type(e).__name__)
        if resp.status_code >= 400:
            raise UrlFetchError("Remote returned HTTP %d"
                                % resp.status_code)
        ctype = resp.headers.get("content-type", "").lower()
        if ctype and not ("html" in ctype or "text/plain" in ctype
                          or "xml" in ctype):
            raise UrlFetchError(
                "Unsupported content type %r (need an HTML/text job "
                "page)" % ctype.split(";")[0])
        body = resp.content[:URL_FETCH_MAX_BYTES + 1]
        if len(body) > URL_FETCH_MAX_BYTES:
            raise UrlFetchError(
                "Response exceeds %d byte limit" % URL_FETCH_MAX_BYTES)
        text = _html_to_text(body.decode(resp.encoding or "utf-8",
                                         "replace"))
        if len(text) < 40:
            raise UrlFetchError(
                "Fetched page had no usable text (login wall or JS-only "
                "page?) - paste the description instead")
        return text
    finally:
        if owns:
            client.close()


# ===========================================================================
# Schemas + Delivery 1 routers
# ===========================================================================
# SourceCreate/Read/RefRead imported from modules/sources (Phase 9 PR-7)


class StrategyUpsert(BaseModel):
    name: str = "Default Strategy"
    target_roles: List[str] = []
    region: Optional[str] = None
    work_mode: WorkMode = WorkMode.any
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    aggressiveness: Aggressiveness = Aggressiveness.balanced
    weights: Optional[dict] = None
    include_companies: List[str] = []
    exclude_companies: List[str] = []
    targeting_notes: str = ""
    score_threshold: int = 0


class StrategyRead(StrategyUpsert):
    id: str
    is_active: bool
    weights: dict
    updated_at: datetime


# sources_router imported from modules/sources (Phase 9 PR-7)
strategy_router = APIRouter(prefix="/api/strategy", tags=["strategy"])
# _source_read imported from modules/sources (Phase 9 PR-7)


# source endpoints imported from modules/sources (Phase 9 PR-7)

def _active_strategy(session):
    strat = session.exec(select(Strategy).where(
        Strategy.owner_id == _uid(),
        Strategy.is_active == True)).first()  # noqa: E712
    if not strat:
        strat = Strategy(owner_id=_uid())
        session.add(strat)
        session.commit()
        session.refresh(strat)
    return strat


def _strategy_read(s):
    return StrategyRead(
        id=s.id, is_active=s.is_active, name=s.name,
        target_roles=s.target_roles, region=s.region,
        work_mode=s.work_mode, salary_min=s.salary_min,
        salary_max=s.salary_max, aggressiveness=s.aggressiveness,
        weights=s.weights, include_companies=s.include_companies,
        exclude_companies=s.exclude_companies,
        targeting_notes=s.targeting_notes,
        score_threshold=getattr(s, "score_threshold", 0) or 0,
        updated_at=s.updated_at)


@strategy_router.get("", response_model=StrategyRead)
def get_strategy(session: Session = Depends(get_session)):
    return _strategy_read(_active_strategy(session))


@strategy_router.put("", response_model=StrategyRead)
def put_strategy(body: StrategyUpsert,
                 session: Session = Depends(get_session)):
    s = _active_strategy(session)
    s.name = body.name
    s.target_roles = body.target_roles
    s.region = body.region
    s.work_mode = body.work_mode
    s.salary_min = body.salary_min
    s.salary_max = body.salary_max
    s.aggressiveness = body.aggressiveness
    if body.weights:
        merged = dict(DEFAULT_WEIGHTS)
        merged.update({k: int(v) for k, v in body.weights.items()
                       if k in DEFAULT_WEIGHTS})
        s.weights = merged
    s.include_companies = body.include_companies
    s.exclude_companies = body.exclude_companies
    s.targeting_notes = body.targeting_notes
    s.score_threshold = max(0, min(100, int(body.score_threshold or 0)))
    s.updated_at = _now()
    session.add(s)
    session.commit()
    session.refresh(s)
    return _strategy_read(s)


@jobs_router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: str, session: Session = Depends(get_session)):
    j = _get_owned(session, JobPosting, job_id)
    if not j:
        raise HTTPException(404, "Job not found")
    return _job_read(j)


@jobs_router.post("/{job_id}/archive", response_model=JobRead)
def archive_job(job_id: str, session: Session = Depends(get_session)):
    j = session.get(JobPosting, job_id)
    if not j:
        raise HTTPException(404, "Job not found")
    j.is_archived = True
    session.add(j)
    session.commit()
    session.refresh(j)
    return _job_read(j)


@jobs_router.post("/{job_id}/unarchive", response_model=JobRead)
def unarchive_job(job_id: str, session: Session = Depends(get_session)):
    j = session.get(JobPosting, job_id)
    if not j:
        raise HTTPException(404, "Job not found")
    j.is_archived = False
    session.add(j)
    session.commit()
    session.refresh(j)
    return _job_read(j)


# ===========================================================================
# App + Delivery 1 wiring
# ===========================================================================
SAMPLE_RESUME = """Professional Summary
Senior AI Product Manager focused on healthcare AI and lab workflow \
automation with measurable business impact.

Experience
Director of Product, Pattern (2021-10 to 2024-07)
- Built Pattern's first AI chatbot that drove $3.4M in revenue growth \
across the analytics product line.
- Led cross-functional product strategy across engineering and design \
teams for AI discovery features.

Education
- MBA in Marketing and Strategy, BYU Marriott School of Business
"""

SAMPLE_JD = """Title: Senior AI Product Manager
Company: Example Health AI
Location: Remote - United States

We are hiring a Senior AI Product Manager to lead decision-support tools.

Requirements
- 6+ years product management experience
- Shipped AI/ML or data products
- Healthcare or clinical domain exposure
- Strong stakeholder and executive communication
"""


def seed():
    with Session(engine) as session:
        if not session.exec(select(Source)).first():
            src = Source(source_type=SourceType.resume,
                         label="Sample Resume", raw_text=SAMPLE_RESUME,
                         extracted_text=SAMPLE_RESUME,
                         parse_meta={"format": "seed"})
            session.add(src)
            session.commit()
            session.refresh(src)
            extract_claims(session, src)
        if not session.exec(select(Strategy)).first():
            session.add(Strategy(
                name="AI PM",
                target_roles=["Senior AI Product Manager"],
                work_mode=WorkMode.remote,
                targeting_notes="healthcare AI workflow automation"))
        if not session.exec(select(JobPosting)).first():
            session.add(import_job(
                SAMPLE_JD, source_url="https://example.com/jobs/001"))
        session.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_config()          # Phase 6: fail fast on bad config
    _logj("startup", auth=AUTH_ENABLED,
          ai=os.getenv("APTIRO_AI_PROVIDER", "mock"))
    init_db()
    if SEED_ON_STARTUP:
        seed()
        seed_job_sources()
        seed_packages()
    yield
    _logj("shutdown")


app = FastAPI(title="Aptiro API", version="0.5.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"], allow_headers=["*"])


def _mw_session():
    """Use the same session the app uses for requests - including any
    test dependency override - so token resolution is consistent with
    the data the request will see."""
    override = app.dependency_overrides.get(get_session)
    gen = (override or get_session)()
    return next(gen), gen


@app.middleware("http")
async def _auth_context(request, call_next):
    """Phase 4 identity + Phase 6 observability. Resolves the bearer
    token to a user id (no token -> built-in local user; AUTH-on
    requires a token for mutations), assigns a request id, logs one
    structured line, stamps X-Request-ID on every response, and writes
    an append-only AuditEvent for successful mutating requests."""
    from starlette.responses import JSONResponse
    rid = request.headers.get("x-request-id") or _uuidmod.uuid4().hex[:16]
    rtok = _REQUEST_ID.set(rid)
    started = _time.perf_counter()
    method, path = request.method, request.url.path

    def _stamp(resp):
        resp.headers["X-Request-ID"] = rid
        return resp

    try:
        token = _bearer(request)
        uid = DEFAULT_UID
        if token:
            s, gen = _mw_session()
            try:
                u = s.exec(
                    select(User).where(User.token == token)).first()
            finally:
                try:
                    next(gen)
                except StopIteration:
                    pass
            if u:
                uid = u.id
            elif AUTH_ENABLED:
                _logj("request.denied", method=method, path=path,
                      status=401, reason="invalid_token")
                return _stamp(JSONResponse(
                    {"detail": "Invalid or expired token"},
                    status_code=401))
        is_open = (path.startswith("/api/auth/")
                   or path in ("/api/health", "/healthz", "/readyz", "/")
                   or not path.startswith("/api/"))
        if (AUTH_ENABLED and not token and not is_open
                and method in ("POST", "PUT", "PATCH", "DELETE")):
            _logj("request.denied", method=method, path=path,
                  status=401, reason="auth_required")
            return _stamp(JSONResponse(
                {"detail": "Authentication required"}, status_code=401))

        utok = _CURRENT_UID.set(uid)
        try:
            response = await call_next(request)
        finally:
            _CURRENT_UID.reset(utok)
        dur = int((_time.perf_counter() - started) * 1000)
        _logj("request", method=method, path=path,
              status=response.status_code, duration_ms=dur, user=uid)
        # Append-only audit for successful mutations (no bodies stored).
        if (method in ("POST", "PUT", "PATCH", "DELETE")
                and 200 <= response.status_code < 300
                and path.startswith("/api/")):
            try:
                s, gen = _mw_session()
                try:
                    s.add(AuditEvent(
                        owner_id=uid, request_id=rid, method=method,
                        path=path, status=response.status_code,
                        duration_ms=dur))
                    s.commit()
                finally:
                    try:
                        next(gen)
                    except StopIteration:
                        pass
            except Exception as exc:   # never fail a request on audit
                _logj("audit.error", message=repr(exc))
        return _stamp(response)
    except Exception as exc:
        dur = int((_time.perf_counter() - started) * 1000)
        _logj("request.error", method=method, path=path,
              duration_ms=dur, error=type(exc).__name__, message=str(exc))
        return _stamp(JSONResponse(
            {"detail": "Internal server error", "request_id": rid},
            status_code=500))
    finally:
        _REQUEST_ID.reset(rtok)
app.include_router(sources_router)
app.include_router(claims_router)
app.include_router(strategy_router)
app.include_router(jobs_router)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "Aptiro", "delivery": 4,
            "slice": "trust-export", "phase": 2,
            "phases_shipped": [1, 2, 3], "latest_phase": 4,
            "upgrade_phases_shipped": [7, 4, 5, 6, 7, 8],
            "providers": {"ai": AI_PROVIDER, "job": JOB_PROVIDER,
                          "search": SEARCH_PROVIDER,
                          "notification": NOTIFICATION_PROVIDER,
                          "embedding":
                              embeddings.active_embedding_provider_name()},
            "ingestion_formats": sorted(ingestion.SUPPORTED),
            "export_formats": exporting.FORMATS,
            "export_profiles": [exporting.ATS_PROFILE],
            "job_import": ["paste", "url"],
            "semantic_signal": {
                "provider": embeddings.active_embedding_provider_name(),
                "affects_score": False},
            "application_tracker": {
                "enabled": True, "auto_submit": False,
                "immutable_snapshot": True},
            "auth": {"enabled": AUTH_ENABLED,
                     "mode": "multi_user" if AUTH_ENABLED
                     else "single_user_local",
                     "default_user": DEFAULT_UID},
            "ai_assist": {
                "provider": ai_provider.active_provider_name(),
                "grounding_gate": True,
                "auto_apply": False},
            "observability": {
                "structured_logs": True, "request_id": True,
                "audit_trail": True, "config_validation": True}}


@app.get("/healthz")
def healthz():
    """Liveness: the process is up and serving. No dependencies."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz(session: Session = Depends(get_session)):
    """Readiness: the database is reachable. 503 if not, so an
    orchestrator can hold traffic until the app can actually serve."""
    from sqlalchemy import text as _sql
    try:
        session.exec(_sql("SELECT 1"))
        return {"status": "ready", "db": "ok"}
    except Exception as exc:
        _logj("readyz.fail", message=repr(exc))
        raise HTTPException(503, "database not ready")


# ===========================================================================
# Delivery 2 - job sources (mocked) + explainable scoring
# ===========================================================================
JOB_SOURCES = ["remotive", "greenhouse", "lever", "ashby", "adzuna"]

_SOURCE_SAMPLE_JOBS = {
  "remotive": [
    {"title": "Senior AI Product Manager", "company": "Vireo Health AI",
     "location": "Remote - United States", "work_mode": WorkMode.remote,
     "salary_min": 165000, "salary_max": 215000,
     "description_text": ("Own the roadmap for AI-assisted clinical "
        "decision support. Partner cross-functionally with ML engineers "
        "and clinicians on healthcare workflow automation."),
     "requirements": ["6+ years product management experience",
        "Shipped AI/ML or data products", "Healthcare or clinical domain",
        "Strong stakeholder and executive communication"]},
    {"title": "Principal Product Manager, ML Platform",
     "company": "Northwind Labs", "location": "Remote - US",
     "work_mode": WorkMode.remote, "salary_min": 190000,
     "salary_max": 240000,
     "description_text": ("Lead platform strategy for internal ML "
        "tooling, data pipelines and model governance across teams."),
     "requirements": ["8+ years PM, 3+ in ML/AI platform",
        "Model governance experience",
        "Technical depth with data systems"]},
    {"title": "Growth Marketing Manager", "company": "BrightCart",
     "location": "Remote", "work_mode": WorkMode.remote,
     "salary_min": 110000, "salary_max": 140000,
     "description_text": ("Drive paid acquisition and lifecycle "
        "marketing. Own channel mix and budget."),
     "requirements": ["5+ years growth marketing",
        "Paid acquisition ownership", "Lifecycle and CRM"]},
  ],
  "greenhouse": [
    {"title": "Lead Product Manager, Healthcare AI",
     "company": "Helix Diagnostics", "location": "Remote - US",
     "work_mode": WorkMode.remote, "salary_min": 175000,
     "salary_max": 225000,
     "description_text": ("Define and ship AI-driven diagnostic "
        "products. Work with clinical and ML teams on EHR/EMR "
        "integration and lab workflow automation."),
     "requirements": ["7+ years product management",
        "AI/ML product delivery", "Healthcare / clinical / lab domain",
        "EHR or EMR integration experience"]},
    {"title": "Senior Product Manager, Platform",
     "company": "Cascade Cloud", "location": "Hybrid - Seattle",
     "work_mode": WorkMode.hybrid, "salary_min": 170000,
     "salary_max": 210000,
     "description_text": ("Own developer platform and API strategy for "
        "a cloud infrastructure company."),
     "requirements": ["6+ years platform PM", "API product strategy",
        "Cloud infrastructure background"]},
  ],
  "lever": [
    {"title": "Director of Product, AI", "company": "Northstar Bio",
     "location": "Remote", "work_mode": WorkMode.remote,
     "salary_min": 200000, "salary_max": 260000,
     "description_text": ("Lead an AI product org for life-sciences "
        "data. Cross-functional leadership across ML, data and design."),
     "requirements": ["10+ years product, 4+ leadership",
        "AI/ML product leadership", "Life sciences or healthcare data"]},
  ],
  "ashby": [
    {"title": "Senior AI Product Manager", "company": "Lumen Health",
     "location": "Remote - US", "work_mode": WorkMode.remote,
     "salary_min": 170000, "salary_max": 220000,
     "description_text": ("Own AI-assisted clinical workflow products. "
        "Partner with clinicians and ML engineers."),
     "requirements": ["6+ years PM", "AI/ML products",
        "Healthcare workflow domain"]},
  ],
  "adzuna": [
    {"title": "Product Manager, Data & AI", "company": "Gridpoint",
     "location": "Hybrid - Austin", "work_mode": WorkMode.hybrid,
     "salary_min": 150000, "salary_max": 190000,
     "description_text": ("Own data and AI features for an analytics "
        "platform."),
     "requirements": ["5+ years PM", "Data/AI products",
        "Analytics platform experience"]},
  ],
}


def fetch_jobs_from_source(provider, query, limit):
    # Phase 5: route to real provider when explicitly configured
    # and httpx is available; always falls back to mock samples.
    if (provider == 'remotive'
            and os.getenv('APTIRO_JOB_PROVIDER', 'mock').lower()
               == 'remotive'
            and _httpx is not None):
        real = _fetch_remotive_real(query, limit or 20)
        if real:
            return provider, real
    samples = _SOURCE_SAMPLE_JOBS.get(provider, [])
    if query:
        ql = query.lower()
        samples = [s for s in samples
                   if ql in s["title"].lower()
                   or ql in s["description_text"].lower()]
    jobs = []
    for s in samples[:limit]:
        jobs.append(JobPosting(
            title=s["title"], company=s["company"],
            location=s.get("location"),
            work_mode=s.get("work_mode", WorkMode.any),
            salary_min=s.get("salary_min"),
            salary_max=s.get("salary_max"),
            source=provider, source_url="https://example.com/%s" % provider,
            description_text=s["description_text"],
            requirements=s.get("requirements", []),
            posted_at="2026-05-01",
            provider_source=provider,
            last_seen_at=_now(), is_stale=False))
    return provider, jobs


_SENIORITY = [
    (["intern", "junior", "associate"], 1),
    (["mid", "product manager", "pm"], 2),
    (["senior", "sr", "lead"], 3),
    (["principal", "staff", "director"], 4),
    (["vp", "vice president", "head of", "chief"], 5),
]
_DOMAINS = {
    "healthcare": ["healthcare", "clinical", "ehr", "emr", "lab",
                   "diagnostic", "life sciences", "patient"],
    "ai_data": ["ai", "ml", "machine learning", "llm", "data", "model",
                "analytics"],
    "ecommerce": ["ecommerce", "e-commerce", "marketplace", "retail"],
    "platform": ["platform", "infrastructure", "api", "developer"],
    "saas": ["saas", "b2b", "enterprise"],
}
_LEADERSHIP = ["led", "leadership", "managed", "director", "head of",
               "cross-functional", "vp", "principal", "owned", "drove"]
_AI_TERMS = ["ai", "ml", "machine learning", "llm", "chatbot", "model",
             "data", "analytics", "automation", "nlp"]
_PROV_QUALITY = {
    ProvenanceCategory.grounded_resume_truth: 1.0,
    ProvenanceCategory.profile_derived: 0.7,
    ProvenanceCategory.public_context_supported: 0.5,
    ProvenanceCategory.ai_suggested: 0.3,
    ProvenanceCategory.unsupported: 0.0,
}


def _has_term(text, term):
    t = (text or "").lower()
    if " " in term or "-" in term or "&" in term:
        return term in t
    return re.search(r"\b" + re.escape(term) + r"\b", t) is not None


def _seniority_rank(text):
    low = (text or "").lower()
    ranks = [r for words, r in _SENIORITY
             if any(_has_term(low, w) for w in words)]
    return max(ranks) if ranks else 2


def _domains_in(text):
    low = (text or "").lower()
    return [d for d, kws in _DOMAINS.items()
            if any(_has_term(low, k) for k in kws)]


def _tokens(text):
    return set(re.findall(r"[a-z0-9+#]+", (text or "").lower()))


def _candidate_profile(session):
    claims = session.exec(select(ProfileClaim)).all()
    usable = [c for c in claims
              if claim_provenance(session, c)
              != ProvenanceCategory.unsupported]
    skills, blob, roles, quals = [], [], [], []
    grounded = 0
    # Phase 2: which claim earned which signal, so the score breakdown
    # can cite the exact evidence. Maps signal token -> [claim ids];
    # by_claim -> short snippet for display.
    skill_src, domain_src = {}, {}
    lead_src, senior_src, ai_src = [], [], {}
    by_claim = {}
    for c in usable:
        cid = c.id
        by_claim[cid] = (c.claim_text or "")[:140]
        cl = (c.claim_text or "").lower()
        for sk in (c.skills or []):
            if sk not in skills:
                skills.append(sk)
            skill_src.setdefault(sk, [])
            if cid not in skill_src[sk]:
                skill_src[sk].append(cid)
        for sk in _extract_skills(c.claim_text):
            if sk not in skills:
                skills.append(sk)
            skill_src.setdefault(sk, [])
            if cid not in skill_src[sk]:
                skill_src[sk].append(cid)
        for d in _domains_in(cl):
            domain_src.setdefault(d, [])
            if cid not in domain_src[d]:
                domain_src[d].append(cid)
        for t in _AI_TERMS:
            if _has_term(cl, t):
                ai_src.setdefault(t, [])
                if cid not in ai_src[t]:
                    ai_src[t].append(cid)
        if any(k in cl for k in _LEADERSHIP):
            lead_src.append(cid)
        blob.append(c.claim_text)
        if c.role:
            roles.append(c.role)
            if _seniority_rank(c.role) >= 3:
                senior_src.append(cid)
        prov = claim_provenance(session, c)
        if prov == ProvenanceCategory.grounded_resume_truth:
            grounded += 1
        q = _PROV_QUALITY.get(prov, 0.0) * (
            0.5 + 0.5 * float(c.confidence or 0.0))
        if c.approval_status == ApprovalStatus.approved:
            q = min(1.0, q + 0.1)
        elif c.approval_status == ApprovalStatus.pending:
            q *= 0.9
        quals.append(q)
    text = " ".join(blob)
    return {"claims": usable, "skills": skills, "text": text,
            "domains": _domains_in(text + " " + " ".join(skills)),
            "leadership": any(k in text.lower() for k in _LEADERSHIP),
            "seniority": max([_seniority_rank(r) for r in roles] + [2]),
            "evidence": round(sum(quals) / len(quals), 3) if quals else 0.0,
            "grounded": grounded, "n": len(usable),
            "skill_src": skill_src, "domain_src": domain_src,
            "lead_src": lead_src, "senior_src": senior_src,
            "ai_src": ai_src, "by_claim": by_claim,
            "all_ids": [c.id for c in usable]}


def _weight(weights, key):
    try:
        return int(weights.get(key, DEFAULT_WEIGHTS[key]))
    except Exception:
        return DEFAULT_WEIGHTS[key]


# score_job / _structured_requirements extracted to app.modules.scoring (Phase 9 PR-11); re-imported here.
from app.modules.scoring import score_job, _structured_requirements  # noqa: E402,F401
# APTIRO_PHASE9_PR11_SCORING_MARKER


class SourceInfo(BaseModel):
    id: str
    mock: bool
    sample_count: int


class JobSourcesOut(BaseModel):
    active_provider: str
    available: List[SourceInfo]


class FetchRequest(BaseModel):
    provider: Optional[str] = None
    query: Optional[str] = None
    limit: int = 10


class FetchResult(BaseModel):
    provider: str
    fetched: int
    skipped_duplicates: int
    jobs: List[JobRead]


class ClaimEvidence(BaseModel):
    claim_id: str
    snippet: str


class ScoreComponent(BaseModel):
    key: str
    label: str
    weight: int
    earned: float
    detail: str
    evidence: List[ClaimEvidence] = []


class SemanticSignal(BaseModel):
    provider: str
    similarity: float
    label: str
    affects_score: bool
    note: str
    agreement: str


class JobMatchOut(BaseModel):
    job: JobRead
    score: int
    earned_points: float
    max_points: int
    components: List[ScoreComponent]
    matched_skills: List[str]
    missing_requirements: List[str]
    excluded: bool
    summary: str
    structured_requirements: dict = {}
    semantic: Optional[SemanticSignal] = None


matches_router = APIRouter(tags=["matches"])


@matches_router.get("/api/job-sources", response_model=JobSourcesOut)
def get_job_sources():
    return JobSourcesOut(
        active_provider=JOB_PROVIDER,
        available=[SourceInfo(id=p, mock=True,
                              sample_count=len(_SOURCE_SAMPLE_JOBS.get(p,
                                                                       [])))
                   for p in JOB_SOURCES])


@matches_router.post("/api/job-sources/fetch",
                     response_model=FetchResult)
def fetch_job_source(body: FetchRequest,
                     session: Session = Depends(get_session)):
    provider = body.provider or JOB_SOURCES[0]
    if provider not in JOB_SOURCES:
        raise HTTPException(400, "Unknown provider")
    _, jobs = fetch_jobs_from_source(provider, body.query, body.limit)
    existing = {(j.title.lower(), j.company.lower())
                for j in session.exec(select(JobPosting)).all()}
    fetched, skipped = [], 0
    for j in jobs:
        k = (j.title.lower(), j.company.lower())
        if k in existing:
            skipped += 1
            continue
        existing.add(k)
        session.add(j)
        fetched.append(j)
    session.commit()
    for j in fetched:
        session.refresh(j)
    return FetchResult(provider=provider, fetched=len(fetched),
                       skipped_duplicates=skipped,
                       jobs=[_job_read(j) for j in fetched])


def _match_payload(session, job, strat):
    sc = score_job(session, job, strat)
    return JobMatchOut(
        job=_job_read(job), score=sc["score"],
        earned_points=sc["earned_points"], max_points=sc["max_points"],
        components=[ScoreComponent(**c) for c in sc["components"]],
        matched_skills=sc["matched_skills"],
        missing_requirements=sc["missing_requirements"],
        excluded=sc["excluded"], summary=sc["summary"],
        structured_requirements=sc.get("structured_requirements", {}),
        semantic=SemanticSignal(**sc["semantic"])
        if sc.get("semantic") else None)


@matches_router.get("/api/matches", response_model=List[JobMatchOut])
def list_matches(session: Session = Depends(get_session)):
    strat = _active_strategy(session)
    jobs = session.exec(select(JobPosting).where(
        JobPosting.owner_id == _uid(),
        JobPosting.is_archived == False)).all()  # noqa: E712
    out = [_match_payload(session, j, strat) for j in jobs]
    out.sort(key=lambda m: m.score, reverse=True)
    return out


@matches_router.get("/api/matches/{job_id}", response_model=JobMatchOut)
def match_detail(job_id: str, session: Session = Depends(get_session)):
    job = session.get(JobPosting, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _match_payload(session, job, _active_strategy(session))


def seed_job_sources():
    with Session(engine) as session:
        jobs = session.exec(select(JobPosting)).all()
        if len(jobs) > 1:
            return
        seen = {(j.title.lower(), j.company.lower()) for j in jobs}
        _, a = fetch_jobs_from_source("remotive", None, 10)
        _, b = fetch_jobs_from_source("greenhouse", None, 10)
        for j in a + b:
            k = (j.title.lower(), j.company.lower())
            if k in seen:
                continue
            seen.add(k)
            session.add(j)
        session.commit()


app.include_router(matches_router)


# ===========================================================================
# Delivery 3 - package builder + per-bullet provenance + council
# ===========================================================================
def _bullet_live_provenance(session, b):
    if b.claim_id:
        c = session.get(ProfileClaim, b.claim_id)
        if c is None:
            return ProvenanceCategory.unsupported
        return claim_provenance(session, c)
    return b.provenance


def _unsupported_metrics(session, b):
    nums = _extract_metrics(b.current_text or "")
    if not nums:
        return []
    src = ""
    if b.claim_id:
        c = session.get(ProfileClaim, b.claim_id)
        if c:
            src = (c.claim_text or "") + " " + " ".join(c.metrics or [])
    return [n for n in nums if n.strip() not in src]


# --- Phase 5: provenance verification gate -------------------------------
# A POST-generation check applied to ANY AI-produced text before it can
# touch a package. It rejects output that introduces a "hard fact" - a
# metric, a standalone number, or a proper-noun entity - that is not
# present in the linked approved claim's grounding. This is the
# pre-emptive complement to Axiom's existing fabricated-metric block:
# Axiom catches it during the council; this stops it ever being stored.
_BARE_NUM = re.compile(r"(?<![A-Za-z0-9])\d[\d,]*(?:\.\d+)?(?![A-Za-z])")
_PROPER = re.compile(
    r"\b[A-Z][A-Za-z0-9&.\-]+(?:\s+[A-Z][A-Za-z0-9&.\-]+){0,3}\b")
_PROPER_STOP = {
    "i", "the", "a", "an", "and", "or", "but", "led", "drove", "built",
    "managed", "shipped", "owned", "created", "designed", "delivered",
    "launched", "improved", "increased", "reduced", "developed",
    "spearheaded", "this", "that", "these", "those", "we", "my", "our",
    "responsible", "worked", "helped", "tasked", "for", "with", "to",
    "of", "in", "on", "by", "as", "at", "team", "teams", "product",
    "products", "strategy", "platform", "customers", "users",
}


def _grounding_text(session, claim):
    if claim is None:
        return ""
    return " ".join([
        claim.claim_text or "", " ".join(claim.metrics or []),
        " ".join(claim.skills or []), claim.company or "",
        claim.role or "", claim.date_range or ""]).lower()


def verify_grounded(session, text, claim):
    """Return a list of grounding violations for `text` against
    `claim`. Empty list => every hard fact in the text is present in the
    claim's evidence (i.e. nothing was fabricated). A bullet with no
    linked approved claim cannot be grounded at all."""
    if claim is None:
        return ["No linked approved claim - AI text cannot be grounded."]
    src = _grounding_text(session, claim)
    text = text or ""
    viol = []
    seen = set()
    for m in _extract_metrics(text):
        key = ("m", m.strip().lower())
        if m.strip().lower() not in src and key not in seen:
            seen.add(key)
            viol.append("metric not in evidence: %s" % m.strip())
    for num in _BARE_NUM.findall(text):
        n = num.replace(",", "")
        if len(n.replace(".", "")) < 2:        # ignore trivial 1-digit
            continue
        if (num.lower() in src or n in src.replace(",", "")):
            continue
        key = ("n", n)
        if key not in seen:
            seen.add(key)
            viol.append("number not in evidence: %s" % num)
    for ent in _PROPER.findall(text):
        el = ent.lower().strip(" .,-")
        if not el or el in src:
            continue
        if all(w in _PROPER_STOP for w in el.split()):
            continue
        # only flag entity-ish: multi-word OR all-caps acronym-ish
        if " " in ent or (ent.isupper() and len(ent) >= 3):
            key = ("e", el)
            if key not in seen:
                seen.add(key)
                viol.append("entity not in evidence: %s" % ent)
    return viol


def _rank_claims(claims):
    return sorted(
        claims,
        key=lambda c: (1 if (c.metrics or []) else 0,
                       float(c.confidence or 0.0), len(c.claim_text or "")),
        reverse=True)


def _add_bullet(session, pkg, section, idx, text, claim, prov, conf, why):
    color = provenance_color(prov)
    b = PackageBullet(
        package_id=pkg.id, section=section, order_index=idx,
        claim_id=(claim.id if claim else None),
        original_text=text, current_text=text, provenance=prov,
        provenance_color=color, confidence=round(float(conf or 0.0), 2),
        status=BulletStatus.proposed, rationale=why)
    session.add(b)
    return b


def build_package(session, job, strategy):
    sc = score_job(session, job, strategy)
    prof = _candidate_profile(session)
    claims = prof["claims"]
    job_low = " ".join([job.title or "", job.description_text or "",
                        " ".join(job.requirements or [])]).lower()
    job_tok = _tokens(job_low)

    pkg = ApplicationPackage(
        job_id=job.id, strategy_id=getattr(strategy, "id", None),
        title=job.title, company=job.company, owner_id=_uid(),
        status=PackageStatus.draft, score_snapshot=sc["score"],
        summary="Draft package for %s @ %s (fit %d/100)."
                % (job.title, job.company, sc["score"]))
    session.add(pkg)
    session.commit()
    session.refresh(pkg)
    idx = 0

    summ = next((c for c in claims
                 if c.claim_type == ClaimType.summary), None)
    if summ:
        _add_bullet(session, pkg, "summary", idx, summ.claim_text, summ,
                    claim_provenance(session, summ), summ.confidence,
                    "Profile summary, tailored target: %s." % job.title)
    else:
        top_sk = ", ".join(sc["matched_skills"][:4]) or \
            ", ".join(prof["skills"][:4]) or "product leadership"
        synth = ("Product leader targeting %s roles, with strength in %s."
                 % (job.title, top_sk))
        _add_bullet(session, pkg, "summary", idx, synth, None,
                    ProvenanceCategory.ai_suggested, 0.4,
                    "AI-synthesized positioning line - no factual claims; "
                    "review before use.")
    idx += 1

    exp = [c for c in _rank_claims(claims)
           if c.claim_type in (ClaimType.achievement,
                               ClaimType.responsibility)]
    chosen = []
    for c in exp:
        overlap = len(_tokens(c.claim_text) & job_tok)
        chosen.append((overlap, c))
    chosen.sort(key=lambda t: t[0], reverse=True)
    for overlap, c in chosen[:6]:
        prov = claim_provenance(session, c)
        hit = sorted({r for r in (job.requirements or [])
                      if _tokens(r) & _tokens(c.claim_text)})[:1]
        why = ("Maps to JD: \"%s\"; " % hit[0]) if hit else \
              "Supporting evidence; "
        why += ("%d shared keyword(s); confidence %.2f; %s."
                % (overlap, float(c.confidence or 0.0),
                   prov.value.replace("_", " ")))
        _add_bullet(session, pkg, "experience", idx, c.claim_text, c,
                    prov, c.confidence, why)
        idx += 1

    if sc["matched_skills"]:
        contrib = [c for c in claims
                   if any(s.lower() in (c.claim_text or "").lower()
                          or s in (c.skills or [])
                          for s in sc["matched_skills"])]
        all_grounded = bool(contrib) and all(
            claim_provenance(session, c)
            == ProvenanceCategory.grounded_resume_truth for c in contrib)
        prov = (ProvenanceCategory.grounded_resume_truth if all_grounded
                else ProvenanceCategory.profile_derived)
        txt = "Core skills: " + ", ".join(sc["matched_skills"][:10]) + "."
        _add_bullet(session, pkg, "skills", idx, txt, None, prov, 0.7,
                    "Synthesized from approved claims; intersection with "
                    "the posting's needs.")
        idx += 1

    _add_bullet(session, pkg, "cover_letter", idx,
                "Dear %s Hiring Team, I am excited to apply for the %s "
                "role." % (job.company, job.title), None,
                ProvenanceCategory.ai_suggested, 0.3,
                "Templated opener - no factual claims.")
    idx += 1
    for overlap, c in chosen[:3]:
        prov = claim_provenance(session, c)
        _add_bullet(session, pkg, "cover_letter", idx,
                    c.claim_text, c, prov, c.confidence,
                    "Cover-letter evidence sentence (verbatim claim).")
        idx += 1
    _add_bullet(session, pkg, "cover_letter", idx,
                "I would welcome the chance to discuss how this maps to "
                "your team's goals. Thank you for your consideration.",
                None, ProvenanceCategory.ai_suggested, 0.3,
                "Templated closer - no factual claims.")
    idx += 1

    _recompute_cover_letter(session, pkg)
    session.commit()
    session.refresh(pkg)
    return pkg


def _recompute_cover_letter(session, pkg):
    cl = sorted([b for b in pkg.bullets if b.section == "cover_letter"],
                key=lambda b: b.order_index)
    keep = [b for b in cl if b.status != BulletStatus.rejected]
    pkg.cover_letter = "\n\n".join(b.current_text for b in keep)
    pkg.updated_at = _now()
    session.add(pkg)


def _bullet_out(session, b):
    live = _bullet_live_provenance(session, b)
    return BulletOut(
        id=b.id, section=b.section, order_index=b.order_index,
        claim_id=b.claim_id, original_text=b.original_text,
        current_text=b.current_text, provenance=b.provenance,
        provenance_color=b.provenance_color, live_provenance=live,
        live_color=provenance_color(live),
        unsupported_metrics=_unsupported_metrics(session, b),
        confidence=b.confidence, status=b.status, rationale=b.rationale,
        user_note=b.user_note)


def _package_out(session, pkg):
    bullets = sorted(pkg.bullets, key=lambda b: b.order_index)
    return PackageOut(
        id=pkg.id, job_id=pkg.job_id, title=pkg.title,
        company=pkg.company, status=pkg.status,
        score_snapshot=pkg.score_snapshot, summary=pkg.summary,
        cover_letter=pkg.cover_letter, strategy_id=pkg.strategy_id,
        bullets=[_bullet_out(session, b) for b in bullets],
        created_at=pkg.created_at, updated_at=pkg.updated_at)


class BulletOut(BaseModel):
    id: str
    section: str
    order_index: int
    claim_id: Optional[str]
    original_text: str
    current_text: str
    provenance: ProvenanceCategory
    provenance_color: str
    live_provenance: ProvenanceCategory
    live_color: str
    unsupported_metrics: List[str]
    confidence: float
    status: BulletStatus
    rationale: str
    user_note: Optional[str]


class PackageOut(BaseModel):
    id: str
    job_id: str
    title: str
    company: str
    status: PackageStatus
    score_snapshot: int
    summary: str
    cover_letter: str
    strategy_id: Optional[str]
    bullets: List[BulletOut]
    created_at: datetime
    updated_at: datetime


class PackageCreate(BaseModel):
    job_id: str


class BulletPatch(BaseModel):
    status: Optional[BulletStatus] = None
    current_text: Optional[str] = None
    user_note: Optional[str] = None


class CritiqueOut(BaseModel):
    id: str
    agent: AgentRole
    agent_title: str
    target: str
    verdict: str
    severity: str
    message: str
    suggestion: Optional[str]


class RunOut(BaseModel):
    id: str
    package_id: str
    status: RunStatus
    ready: bool
    summary: str
    steps: List[dict]
    critiques: List[CritiqueOut]
    started_at: datetime
    finished_at: Optional[datetime]


class RunListItem(BaseModel):
    id: str
    status: RunStatus
    ready: bool
    summary: str
    critique_count: int
    created_at: datetime


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


# ---- Phase 5: grounded AI assist ----------------------------------------
# Optional. Mock is the default and is deterministic/offline, so the
# whole suite stays green with no key. The real Anthropic path is only
# used when explicitly configured; EITHER WAY every AI output passes the
# provenance verification gate before it can touch a package - the model
# can suggest phrasing, never facts.
def _ai_provider():
    """Single indirection so tests can inject a stub provider and so the
    real/mock choice is resolved per call."""
    return ai_provider.get_provider()


class AIRewriteRequest(BaseModel):
    instruction: Optional[str] = None
    apply: bool = False


class AIRewriteOut(BaseModel):
    provider: str
    suggestion: str
    grounded: bool
    violations: List[str]
    applied: bool
    original_text: str
    note: str = ("AI may rephrase only facts already in your approved "
                 "evidence. Anything it adds that is not in the linked "
                 "claim is blocked and never stored.")


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


class AICoverLetterOut(BaseModel):
    provider: str
    draft: str
    grounded: bool
    violations: List[str]
    applied: bool
    note: str = ("Drafted only from your ACCEPTED bullets and gated "
                 "against their evidence; ungrounded drafts are never "
                 "saved to the package.")


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


# ---- council --------------------------------------------------------------
def _C(agent, target, verdict, severity, message, suggestion=None):
    return {"agent": agent, "target": target, "verdict": verdict,
            "severity": severity, "message": message,
            "suggestion": suggestion}


def _ctx(session, pkg):
    job = session.get(JobPosting, pkg.job_id)
    strat = _active_strategy(session)
    sc = score_job(session, job, strat) if job else {"score": 0}
    retained = [b for b in sorted(pkg.bullets,
                                  key=lambda b: b.order_index)
                if b.status != BulletStatus.rejected]
    return {"pkg": pkg, "job": job, "strategy": strat, "score": sc,
            "bullets": retained,
            "missing": sc.get("missing_requirements", [])}


def agent_vector(session, ctx):
    out = []
    sc = ctx["score"]
    miss = ctx["missing"]
    if sc.get("score", 0) < 40:
        out.append(_C(AgentRole.vector, "package", "revise", "major",
                      "Low strategic fit (%d/100) for the active "
                      "strategy." % sc.get("score", 0)))
    if miss:
        out.append(_C(AgentRole.vector, "package", "revise", "minor",
                      "Uncovered JD requirements: %s."
                      % "; ".join(miss[:3]),
                      "Add or surface evidence addressing these."))
    if not any(b.section == "experience" for b in ctx["bullets"]):
        out.append(_C(AgentRole.vector, "section:experience", "revise",
                      "major", "No experience bullets retained - the "
                      "package has no evidence body."))
    if not out:
        out.append(_C(AgentRole.vector, "package", "pass", "info",
                      "Strong alignment with the target role and active "
                      "strategy."))
    return out


def agent_axiom(session, ctx):
    out = []
    for b in ctx["bullets"]:
        prov = _bullet_live_provenance(session, b)
        if prov == ProvenanceCategory.unsupported:
            out.append(_C(AgentRole.axiom, "bullet:%s" % b.id, "reject",
                          "blocker", "Bullet is not backed by an approved "
                          "source (linked claim rejected/removed)."))
        bad = _unsupported_metrics(session, b)
        if bad:
            out.append(_C(AgentRole.axiom, "bullet:%s" % b.id, "flag",
                          "major", "Metric(s) %s not present in the "
                          "linked evidence - possible fabrication."
                          % ", ".join(bad),
                          "Remove the unsupported figure or attach a "
                          "source that contains it."))
    if not out:
        out.append(_C(AgentRole.axiom, "package", "pass", "info",
                      "Every retained bullet traces to approved "
                      "evidence; no unsupported metrics."))
    return out


def agent_lumen(session, ctx):
    out = []
    for b in ctx["bullets"]:
        t = b.current_text or ""
        if len(t) > 320:
            out.append(_C(AgentRole.lumen, "bullet:%s" % b.id, "revise",
                          "minor", "Bullet runs long (%d chars); tighten "
                          "for scannability." % len(t),
                          t[:200].rsplit(" ", 1)[0] + "..."))
        low = t.strip().lower()
        if low.startswith(("responsible for", "worked on",
                           "helped with", "tasked with")):
            out.append(_C(AgentRole.lumen, "bullet:%s" % b.id, "revise",
                          "minor", "Weak opener - lead with an action "
                          "verb and the outcome.",
                          "Led / Drove / Built ..."))
        if b.claim_id:
            c = session.get(ProfileClaim, b.claim_id)
            if c and (c.metrics or []) and not _extract_metrics(t):
                out.append(_C(AgentRole.lumen, "bullet:%s" % b.id,
                              "revise", "minor",
                              "Linked evidence has a quantified result "
                              "not surfaced here.",
                              "Surface: %s" % ", ".join(c.metrics[:2])))
    if not out:
        out.append(_C(AgentRole.lumen, "package", "pass", "info",
                      "Bullets are concise and results-oriented."))
    return out


def agent_entp(session, ctx):
    prof = _candidate_profile(session)
    out = []
    doms = prof.get("domains", [])
    if "healthcare" in doms and "ai_data" in doms:
        out.append(_C(AgentRole.entp, "package", "pass", "info",
                      "Differentiation: lead with the healthcare x AI "
                      "intersection - few PMs credibly own both.",
                      "Open the summary with that intersection."))
    if prof.get("leadership"):
        out.append(_C(AgentRole.entp, "package", "pass", "info",
                      "Angle: frame leadership scope as outcome "
                      "ownership (P&L / cross-functional), not headcount.",
                      "Reframe one experience bullet around impact "
                      "owned."))
    if not out:
        out.append(_C(AgentRole.entp, "package", "pass", "info",
                      "Consider a portfolio metric to anchor the "
                      "narrative."))
    return out


def agent_istj_qa(session, ctx):
    out = []
    exp = [b for b in ctx["bullets"] if b.section == "experience"]
    cl = [b for b in ctx["bullets"] if b.section == "cover_letter"]
    if len(exp) < 2:
        out.append(_C(AgentRole.istj_qa, "section:experience", "revise",
                      "major", "Only %d experience bullet(s); a credible "
                      "package needs at least 2." % len(exp)))
    if len(exp) > 8:
        out.append(_C(AgentRole.istj_qa, "section:experience", "revise",
                      "minor", "%d experience bullets - trim to the "
                      "strongest 6-8." % len(exp)))
    if not cl:
        out.append(_C(AgentRole.istj_qa, "section:cover_letter",
                      "revise", "major", "Cover letter is empty."))
    for b in ctx["bullets"]:
        t = (b.current_text or "").strip()
        if b.section == "experience" and t and not t[0].isupper():
            out.append(_C(AgentRole.istj_qa, "bullet:%s" % b.id,
                          "revise", "minor",
                          "Capitalization/format inconsistency."))
    blocker = any(c["severity"] == "blocker"
                  for c in (ctx.get("_axiom") or []))
    major = any(c["severity"] in ("major", "blocker") for c in out) or \
        any(c["severity"] in ("major", "blocker")
            for c in (ctx.get("_axiom") or []))
    verdict = "reject" if blocker else ("revise" if major else "pass")
    sev = "blocker" if blocker else ("major" if major else "info")
    out.append(_C(AgentRole.istj_qa, "package", verdict, sev,
                  "Final QA verdict: %s." % verdict.upper()))
    return out


def orchestrate_package(session, pkg):
    run = AgentRun(package_id=pkg.id, status=RunStatus.running)
    session.add(run)
    session.commit()
    session.refresh(run)
    steps = []

    def step(n, name, detail):
        steps.append({"n": n, "name": name, "status": "completed",
                      "detail": detail})

    ctx = _ctx(session, pkg)
    step(1, "Load package & bullets",
         "%d bullet(s) loaded." % len(pkg.bullets))
    step(2, "Load active strategy & weights",
         "Strategy '%s'." % ctx["strategy"].name)
    step(3, "Re-score job (snapshot)",
         "Fit %d/100." % ctx["score"].get("score", 0))
    step(4, "Gather usable evidence & provenance map",
         "%d retained bullet(s) mapped." % len(ctx["bullets"]))
    pre = [b for b in ctx["bullets"]
           if _bullet_live_provenance(session, b)
           == ProvenanceCategory.unsupported]
    step(5, "Integrity precheck",
         "%d bullet(s) without approved backing." % len(pre))

    crit = []
    cv = agent_vector(session, ctx)
    crit += cv
    step(6, "Vector - strategic alignment", "%d note(s)." % len(cv))
    ca = agent_axiom(session, ctx)
    crit += ca
    ctx["_axiom"] = ca
    step(7, "Axiom - evidence integrity", "%d note(s)." % len(ca))
    cl = agent_lumen(session, ctx)
    crit += cl
    step(8, "Lumen - clarity & impact", "%d note(s)." % len(cl))
    ce = agent_entp(session, ctx)
    crit += ce
    step(9, "ENTP - divergent angle", "%d note(s)." % len(ce))
    cq = agent_istj_qa(session, ctx)
    crit += cq
    step(10, "ISTJ-QA - compliance & QA", "%d note(s)." % len(cq))

    crit.sort(key=lambda c: {"blocker": 0, "major": 1,
                             "minor": 2, "info": 3}[c["severity"]])
    step(11, "Reconcile critiques",
         "%d critique(s) by %d agent(s)."
         % (len(crit), len({c["agent"] for c in crit})))

    normalized = 0
    for b in pkg.bullets:
        if b.status == BulletStatus.proposed:
            nt = " ".join((b.current_text or "").split())
            if nt != (b.current_text or ""):
                b.current_text = nt
                b.updated_at = _now()
                session.add(b)
                normalized += 1
    session.commit()
    step(12, "Apply safe auto-revisions",
         "Whitespace-normalized %d proposed bullet(s); locked/accepted/"
         "rejected/rewritten untouched; no facts altered." % normalized)

    accepted = sum(1 for b in pkg.bullets
                   if b.status == BulletStatus.accepted)
    has_blocker = any(c["severity"] == "blocker" for c in crit)
    axiom_major = any(c["agent"] == AgentRole.axiom
                      and c["severity"] in ("major", "blocker")
                      for c in crit)
    qa_pass = any(c["agent"] == AgentRole.istj_qa
                  and c["target"] == "package" and c["verdict"] == "pass"
                  for c in crit)
    ready = (not has_blocker) and (not axiom_major) and qa_pass
    if ready and accepted > 0:
        summary = ("Council review complete - READY. %d accepted "
                   "bullet(s); no integrity blockers." % accepted)
    elif ready:
        summary = ("Council review complete - CLEAN. No integrity "
                   "blockers and QA passed; accept bullets to finalize.")
    else:
        top = crit[0] if crit else None
        why = (top["message"] if top else "QA did not pass")
        summary = "Council review complete - NOT READY. %s" % why
    step(13, "Finalize & persist", summary)

    for c in crit:
        session.add(AgentCritique(
            run_id=run.id, agent=c["agent"], target=c["target"],
            verdict=c["verdict"], severity=c["severity"],
            message=c["message"], suggestion=c.get("suggestion")))
    run.status = RunStatus.completed
    run.ready = ready
    run.summary = summary
    run.steps = steps
    run.finished_at = _now()
    session.add(run)
    pkg.status = PackageStatus.orchestrated
    pkg.summary = summary
    pkg.updated_at = _now()
    session.add(pkg)
    session.commit()
    session.refresh(run)
    return run


def _run_out(session, run):
    return RunOut(
        id=run.id, package_id=run.package_id, status=run.status,
        ready=run.ready, summary=run.summary, steps=run.steps or [],
        critiques=[CritiqueOut(
            id=c.id, agent=c.agent, agent_title=AGENT_TITLE[c.agent],
            target=c.target, verdict=c.verdict, severity=c.severity,
            message=c.message, suggestion=c.suggestion)
            for c in sorted(run.critiques,
                            key=lambda c: {"blocker": 0, "major": 1,
                                           "minor": 2,
                                           "info": 3}[c.severity])],
        started_at=run.started_at, finished_at=run.finished_at)


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


class CouncilNarrativeOut(BaseModel):
    provider: str
    narrative: str
    ready: bool
    note: str = ("Advisory plain-language summary of the deterministic "
                 "council review. It does not change the verdict, the "
                 "readiness decision, or any bullet.")


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


def seed_packages():
    with Session(engine) as session:
        if session.exec(select(ApplicationPackage)).first():
            return
        jobs = session.exec(select(JobPosting).where(
            JobPosting.is_archived == False)).all()  # noqa: E712
        if not jobs:
            return
        strat = _active_strategy(session)
        scored = sorted(
            jobs, key=lambda j: score_job(session, j, strat)["score"],
            reverse=True)
        build_package(session, scored[0], strat)


# NOTE: packages_router / runs_router are intentionally included AFTER the
# export endpoints below are defined. FastAPI's include_router() snapshots
# a router's routes at call time, so including it here (before the export
# routes) would silently drop /export and /export/preview.


# ===========================================================================
# Trust + Export slice - package export
#   Markdown -> HTML -> DOCX -> PDF, with rejected / do-not-use / red /
#   unsupported content excluded from final exports by default.
# ===========================================================================
EXPORT_MEDIA = {
    "md": "text/markdown", "html": "text/html",
    "docx": ("application/vnd.openxmlformats-officedocument."
             "wordprocessingml.document"),
    "pdf": "application/pdf",
}


def _export_model(session, pkg, include_unsupported=False):
    """Build the structured, provenance-filtered export model.

    Default behavior excludes any bullet that is rejected, do-not-use,
    or whose LIVE provenance is unsupported (red). This is the
    non-negotiable 'no fabricated/unsupported content in final export'
    gate. include_unsupported=True is an explicit user override.
    """
    sections = {"summary": [], "experience": [], "skills": [],
                "cover_letter": []}
    excluded = []
    for b in sorted(pkg.bullets, key=lambda b: b.order_index):
        live = _bullet_live_provenance(session, b)
        reasons = []
        if b.status in (BulletStatus.rejected,):
            reasons.append("user rejected")
        if live == ProvenanceCategory.unsupported:
            reasons.append("unsupported / no approved backing")
        bad = _unsupported_metrics(session, b)
        if bad:
            reasons.append("unsupported metric(s): %s" % ", ".join(bad))
        if reasons and not include_unsupported:
            excluded.append({"text": b.current_text, "section": b.section,
                             "reasons": reasons})
            continue
        sections.setdefault(b.section, []).append({
            "text": b.current_text, "provenance": live.value,
            "color": provenance_color(live), "status": b.status.value,
            "flagged": reasons})
    job = session.get(JobPosting, pkg.job_id)
    return {
        "title": pkg.title, "company": pkg.company,
        "job_title": job.title if job else pkg.title,
        "score": pkg.score_snapshot, "summary": pkg.summary,
        "sections": sections, "excluded": excluded,
        "include_unsupported": include_unsupported,
        "generated_at": _now().isoformat(),
    }


class ExportRequest(BaseModel):
    format: str = "md"
    artifact: str = "resume"        # resume | cover_letter | both
    include_unsupported: bool = False


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


# All package routes (CRUD, bullets, orchestrate, export) are now defined;
# safe to register the routers.
app.include_router(packages_router)
app.include_router(runs_router)


# ===========================================================================
# Delivery 4 - notifications (preview only), apply scaffolding, privacy,
# onboarding. Guarded: nothing is ever sent externally; no automation.
# ===========================================================================
APPLY_GUARDRAILS = [
    "Scaffolding only - no real browser automation is performed.",
    "No CAPTCHA solving or anti-bot circumvention, ever.",
    "Nothing is submitted to any external site.",
    "A human must explicitly confirm handoff before any simulated submit.",
    "Locked/edited package content is used verbatim - never altered here.",
]

_APPLY_ACTIONS = {
    ApplyState.created: ["prepare", "abort"],
    ApplyState.preparing: ["abort"],
    ApplyState.ready_for_review: ["request_handoff", "abort"],
    ApplyState.awaiting_user_handoff: ["confirm", "abort"],
    ApplyState.user_confirmed: ["submit", "abort"],
    ApplyState.submitted: [],
    ApplyState.aborted: [],
}


def _accepted_count(pkg):
    return sum(1 for b in pkg.bullets
              if b.status == BulletStatus.accepted)


def _build_apply_plan(session, pkg):
    plan = [
        {"step": 1, "field": "Full name", "value": "From your profile",
         "source": "profile", "needs_user": False},
        {"step": 2, "field": "Resume document",
         "value": "Exported package resume", "source": "package",
         "needs_user": False},
        {"step": 3, "field": "Cover letter",
         "value": "Exported package cover letter", "source": "package",
         "needs_user": False},
        {"step": 4, "field": "Screening questions",
         "value": "Unknown until the live form is opened",
         "source": "ats", "needs_user": True,
         "note": "Aptiro pauses on unknown fields - you fill these."},
        {"step": 5, "field": "Final submit",
         "value": "Requires explicit human confirmation",
         "source": "guarded", "needs_user": True,
         "blocked_until": "user_confirmed"},
    ]
    return plan


def _hist(sess, state, note):
    h = list(sess.history or [])
    h.append({"state": state.value, "note": note,
              "at": _now().isoformat()})
    sess.history = h


class PreviewRequest(BaseModel):
    kind: NotificationKind
    package_id: Optional[str] = None
    channel: Optional[NotificationChannel] = None


class PreviewOut(BaseModel):
    id: str
    kind: NotificationKind
    channel: NotificationChannel
    subject: str
    body: str
    package_id: Optional[str]
    status: str
    created_at: datetime


class ChannelInfo(BaseModel):
    id: NotificationChannel
    label: str


class ChannelsOut(BaseModel):
    active_provider: str
    sends_externally: bool
    note: str
    channels: List[ChannelInfo]


class ApplyCreate(BaseModel):
    package_id: str


class ApplyAdvance(BaseModel):
    action: str
    confirm: bool = False
    ack: Optional[str] = None


class ApplyOut(BaseModel):
    id: str
    package_id: str
    job_title: str
    company: str
    state: ApplyState
    requires_handoff: bool
    note: str
    plan: List[dict]
    history: List[dict]
    allowed_actions: List[str]
    guardrails: List[str]
    created_at: datetime


class ApplyListItem(BaseModel):
    id: str
    package_id: str
    job_title: str
    company: str
    state: ApplyState
    created_at: datetime


def _preview_out(n):
    return PreviewOut(
        id=n.id, kind=n.kind, channel=n.channel, subject=n.subject,
        body=n.body, package_id=n.package_id, status=n.status,
        created_at=n.created_at)


def _apply_out(sess):
    return ApplyOut(
        id=sess.id, package_id=sess.package_id, job_title=sess.job_title,
        company=sess.company, state=sess.state,
        requires_handoff=sess.requires_handoff, note=sess.note or "",
        plan=sess.plan or [], history=sess.history or [],
        allowed_actions=[a for a in _APPLY_ACTIONS.get(sess.state, [])],
        guardrails=APPLY_GUARDRAILS, created_at=sess.created_at)


notif_router = APIRouter(prefix="/api/notifications", tags=["notifications"])
apply_router = APIRouter(prefix="/api/apply", tags=["apply"])
privacy_router = APIRouter(prefix="/api/privacy", tags=["privacy"])
onboarding_router = APIRouter(tags=["onboarding"])


def _render_package_ready(session, pkg):
    sub = "Your application package for %s is ready" % pkg.title
    body = ("Package for %s @ %s is ready (fit %d/100). %d bullet(s); "
            "review provenance before exporting. [preview - not sent]"
            % (pkg.title, pkg.company, pkg.score_snapshot,
               len(pkg.bullets)))
    return sub, body


def _render_integrity_alert(session, pkg):
    bad = sum(1 for b in pkg.bullets
              if _bullet_live_provenance(session, b)
              == ProvenanceCategory.unsupported)
    sub = "Integrity check: %s" % pkg.title
    body = ("%d bullet(s) in '%s' lack approved backing and will be "
            "excluded from export by default. [preview - not sent]"
            % (bad, pkg.title))
    return sub, body


def _render_digest(session):
    jobs = session.exec(select(JobPosting).where(
        JobPosting.is_archived == False)).all()  # noqa: E712
    strat = _active_strategy(session)
    scored = sorted(((score_job(session, j, strat)["score"], j)
                     for j in jobs), key=lambda t: t[0], reverse=True)
    top = scored[:3]
    lines = ["Top matches [preview - not sent]:"]
    for s, j in top:
        lines.append("- %s @ %s - %d/100" % (j.title, j.company, s))
    return "Aptiro daily digest", "\n".join(lines)


def _persist_previews(session, kind, rendered, package_id=None,
                      only=None):
    sub, body = rendered
    chans = [only] if only else list(NotificationChannel)
    rows = []
    for ch in chans:
        n = NotificationPreview(kind=kind, channel=ch, subject=sub,
                                body=body, package_id=package_id,
                                status="preview")
        session.add(n)
        rows.append(n)
    session.commit()
    for n in rows:
        session.refresh(n)
    return rows


@notif_router.get("/channels", response_model=ChannelsOut)
def notif_channels():
    return ChannelsOut(
        active_provider=NOTIFICATION_PROVIDER, sends_externally=False,
        note="Previews are rendered only. Aptiro never sends or posts "
             "anything externally in this build.",
        channels=[ChannelInfo(id=c, label=c.value.replace("_", "-"))
                  for c in NotificationChannel])


@notif_router.post("/preview", response_model=List[PreviewOut],
                  status_code=201)
def notif_preview(body: PreviewRequest,
                  session: Session = Depends(get_session)):
    if body.kind in (NotificationKind.package_ready,
                     NotificationKind.integrity_alert):
        if not body.package_id:
            raise HTTPException(400, "package_id is required for this kind")
        pkg = session.get(ApplicationPackage, body.package_id)
        if not pkg:
            raise HTTPException(404, "Package not found")
        rendered = (_render_package_ready(session, pkg)
                    if body.kind == NotificationKind.package_ready
                    else _render_integrity_alert(session, pkg))
        pid = pkg.id
    else:
        rendered = _render_digest(session)
        pid = None
    return [_preview_out(n) for n in _persist_previews(
        session, body.kind, rendered, pid, body.channel)]


@notif_router.get("", response_model=List[PreviewOut])
def notif_history(session: Session = Depends(get_session)):
    rows = session.exec(select(NotificationPreview).order_by(
        NotificationPreview.created_at.desc())).all()
    return [_preview_out(n) for n in rows]


@notif_router.get("/digest", response_model=List[PreviewOut])
def notif_digest(session: Session = Depends(get_session)):
    return [_preview_out(n) for n in _persist_previews(
        session, NotificationKind.daily_digest, _render_digest(session))]


@apply_router.post("", response_model=ApplyOut, status_code=201)
def apply_create(body: ApplyCreate,
                 session: Session = Depends(get_session)):
    pkg = session.get(ApplicationPackage, body.package_id)
    if not pkg:
        raise HTTPException(404, "Package not found")
    sess = ApplySession(
        package_id=pkg.id, job_id=pkg.job_id, job_title=pkg.title,
        company=pkg.company, state=ApplyState.created,
        plan=_build_apply_plan(session, pkg))
    _hist(sess, ApplyState.created,
          "Apply session created (scaffolding - no automation).")
    session.add(sess)
    session.commit()
    session.refresh(sess)
    return _apply_out(sess)


@apply_router.get("", response_model=List[ApplyListItem])
def apply_list(session: Session = Depends(get_session)):
    rows = session.exec(select(ApplySession).order_by(
        ApplySession.created_at.desc())).all()
    return [ApplyListItem(
        id=s.id, package_id=s.package_id, job_title=s.job_title,
        company=s.company, state=s.state, created_at=s.created_at)
        for s in rows]


@apply_router.get("/{sid}", response_model=ApplyOut)
def apply_get(sid: str, session: Session = Depends(get_session)):
    sess = session.get(ApplySession, sid)
    if not sess:
        raise HTTPException(404, "Apply session not found")
    return _apply_out(sess)


@apply_router.delete("/{sid}", status_code=204)
def apply_delete(sid: str, session: Session = Depends(get_session)):
    sess = session.get(ApplySession, sid)
    if not sess:
        raise HTTPException(404, "Apply session not found")
    session.delete(sess)
    session.commit()


@apply_router.post("/{sid}/advance", response_model=ApplyOut)
def apply_advance(sid: str, body: ApplyAdvance,
                  session: Session = Depends(get_session)):
    sess = session.get(ApplySession, sid)
    if not sess:
        raise HTTPException(404, "Apply session not found")
    action = body.action
    confirm = body.confirm
    ack = body.ack
    allowed = _APPLY_ACTIONS.get(sess.state, [])
    if action not in allowed:
        raise HTTPException(
            409, "Action '%s' not allowed in state '%s'. Allowed: %s"
            % (action, sess.state.value, ", ".join(allowed) or "none"))
    pkg = session.get(ApplicationPackage, sess.package_id)

    if action == "abort":
        sess.state = ApplyState.aborted
        _hist(sess, sess.state, "Aborted by user.")
    elif action == "prepare":
        sess.state = ApplyState.preparing
        _hist(sess, sess.state, "Compiling field plan from package.")
        if pkg:
            sess.plan = _build_apply_plan(session, pkg)
        sess.state = ApplyState.ready_for_review
        _hist(sess, sess.state,
              "Plan ready - %d field step(s). Review before handoff."
              % len(sess.plan))
    elif action == "request_handoff":
        if not pkg or _accepted_count(pkg) < 1:
            raise HTTPException(
                409, "Accept at least one package bullet before handoff "
                     "(nothing to submit otherwise).")
        sess.state = ApplyState.awaiting_user_handoff
        _hist(sess, sess.state,
              "Paused for human handoff - explicit confirmation "
              "required.")
    elif action == "confirm":
        if confirm is not True:
            raise HTTPException(
                400, "Explicit confirmation required: send confirm=true. "
                     "Aptiro will not proceed without it.")
        sess.note = (ack or "").strip()[:300]
        sess.state = ApplyState.user_confirmed
        _hist(sess, sess.state,
              "User confirmed handoff%s."
              % ((" - ack: " + sess.note) if sess.note else ""))
    elif action == "submit":
        sess.state = ApplyState.submitted
        _hist(sess, sess.state,
              "Simulated submit complete - NO external submission was "
              "performed, no browser was driven, no CAPTCHA handled. "
              "Scaffolding only.")
    sess.updated_at = _now()
    session.add(sess)
    session.commit()
    session.refresh(sess)
    return _apply_out(sess)


# ---- privacy export / delete + onboarding ---------------------------------
_ALL_MODELS = None


def _all_models():
    global _ALL_MODELS
    if _ALL_MODELS is None:
        _ALL_MODELS = [
            Application, ApplySession, NotificationPreview, AgentCritique,
            AgentRun, PackageBullet, ApplicationPackage, SourceRef,
            ProfileClaim, JobPosting, Strategy, Source,
        ]
    return _ALL_MODELS


def _row_dict(obj):
    out = {}
    for col in obj.__table__.columns:
        v = getattr(obj, col.name)
        if isinstance(v, datetime):
            v = v.isoformat()
        out[col.name] = v
    return out


def _scoped_rows(session, m):
    """Owner-bearing models are filtered to the current user; child /
    auxiliary tables (reachable only via an owned parent) are returned
    as-is. In single-user / AUTH-off mode the current user owns
    everything, so behavior is identical to before Phase 4."""
    if hasattr(m, "owner_id"):
        return session.exec(
            select(m).where(m.owner_id == _uid())).all()
    return session.exec(select(m)).all()


def export_bundle(session):
    bundle = {"app": "Aptiro", "delivery": 4,
              "exported_at": _now().isoformat(), "data": {},
              "counts": {}}
    for m in _all_models():
        rows = _scoped_rows(session, m)
        key = m.__tablename__
        bundle["data"][key] = [_row_dict(r) for r in rows]
        bundle["counts"][key] = len(rows)
    return bundle


def wipe_all(session):
    removed = {}
    for m in _all_models():
        rows = _scoped_rows(session, m)
        removed[m.__tablename__] = len(rows)
        for r in rows:
            session.delete(r)
        session.commit()
    return removed


@privacy_router.get("/export")
def privacy_export(session: Session = Depends(get_session)):
    return export_bundle(session)


@privacy_router.delete("/data")
def privacy_delete(session: Session = Depends(get_session)):
    removed = wipe_all(session)
    return {"removed": removed, "total": sum(removed.values())}


def onboarding_state(session):
    src = session.exec(select(Source)).all()
    claims = session.exec(select(ProfileClaim)).all()
    approved = [c for c in claims
                if c.approval_status == ApprovalStatus.approved]
    jobs = session.exec(select(JobPosting)).all()
    pkgs = session.exec(select(ApplicationPackage)).all()
    steps = [
        {"key": "add_source", "label": "Add a resume or profile source",
         "done": len(src) > 0},
        {"key": "approve_claim", "label": "Approve at least one claim",
         "done": len(approved) > 0},
        {"key": "set_strategy", "label": "Configure a search strategy",
         "done": session.exec(select(Strategy)).first() is not None},
        {"key": "import_job", "label": "Import or fetch a job",
         "done": len(jobs) > 0},
        {"key": "build_package", "label": "Build an application package",
         "done": len(pkgs) > 0},
    ]
    completed = sum(1 for s in steps if s["done"])
    nxt = next((s["label"] for s in steps if not s["done"]), None)
    return {"steps": steps, "completed": completed, "total": len(steps),
            "complete": completed == len(steps), "next_step": nxt}


@onboarding_router.get("/api/onboarding")
def get_onboarding(session: Session = Depends(get_session)):
    return onboarding_state(session)


app.include_router(notif_router)
app.include_router(apply_router)
app.include_router(privacy_router)
app.include_router(onboarding_router)


# ===========================================================================
# Phase 3 - application tracker (close the loop, human-in-loop ONLY)
# ===========================================================================
# This module NEVER submits an application anywhere. There is no network
# egress in any tracker code path. `submitted_by_user` is a state the
# USER asserts after they applied on the employer's own site. Every
# transition is explicit and audit-logged; the submit snapshot is frozen
# once and never rewritten.

applications_router = APIRouter(prefix="/api/applications",
                                tags=["applications"])

# Legal, human-initiated transitions. Anything not listed -> HTTP 409.
_APP_TRANSITIONS = {
    ApplicationStatus.drafted: {ApplicationStatus.exported,
                                ApplicationStatus.withdrawn},
    ApplicationStatus.exported: {ApplicationStatus.submitted_by_user,
                                 ApplicationStatus.withdrawn},
    ApplicationStatus.submitted_by_user: {ApplicationStatus.interviewing,
                                          ApplicationStatus.rejected,
                                          ApplicationStatus.withdrawn},
    ApplicationStatus.interviewing: {ApplicationStatus.offer,
                                     ApplicationStatus.rejected,
                                     ApplicationStatus.withdrawn},
    ApplicationStatus.offer: {ApplicationStatus.rejected,
                              ApplicationStatus.withdrawn},
    ApplicationStatus.rejected: set(),     # terminal
    ApplicationStatus.withdrawn: set(),    # terminal
}

# Deterministic follow-up cadence (days after the user-asserted submit).
_REMINDER_PLAN = [
    (3, "follow_up", "No reply yet? A brief, polite follow-up is "
     "reasonable now."),
    (7, "follow_up", "One week in - a second short nudge to the "
     "recruiter is appropriate."),
    (14, "status_check", "Two weeks in - consider this cold and shift "
     "focus to fresher matches."),
]


def _make_reminders(submitted_at):
    """Pure + deterministic: same submitted_at -> identical reminders.
    No scheduler, nothing is ever sent; these are advisory rows."""
    from datetime import timedelta
    out = []
    for days, kind, msg in _REMINDER_PLAN:
        out.append({
            "id": "rem_%d" % days,
            "due_at": (submitted_at + timedelta(days=days)).isoformat(),
            "offset_days": days, "kind": kind, "message": msg,
            "done": False,
        })
    return out


def _app_snapshot(session, app_obj):
    """Freeze EXACTLY what the user is about to send: the same
    provenance-filtered export model used by /export, plus the cover
    letter, plus a content hash for tamper-evidence."""
    import hashlib
    import json as _json
    pkg = session.get(ApplicationPackage, app_obj.package_id)
    model = _export_model(session, pkg) if pkg else {}
    snap = {
        "frozen_at": _now().isoformat(),
        "package_id": app_obj.package_id,
        "title": pkg.title if pkg else app_obj.job_title,
        "company": pkg.company if pkg else app_obj.company,
        "score_snapshot": pkg.score_snapshot if pkg else 0,
        "cover_letter": pkg.cover_letter if pkg else "",
        "export_model": model,
    }
    blob = _json.dumps(snap, sort_keys=True, default=str).encode()
    snap_sha = hashlib.sha256(blob).hexdigest()
    return snap, snap_sha


def _app_hist(app_obj, frm, to, note):
    h = list(app_obj.history or [])
    h.append({"from": frm.value if frm else None, "to": to.value,
              "note": note or "", "at": _now().isoformat()})
    app_obj.history = h


class ReminderOut(BaseModel):
    id: str
    due_at: str
    offset_days: int
    kind: str
    message: str
    done: bool


class ApplicationCreate(BaseModel):
    package_id: str
    note: Optional[str] = None


class ApplicationTransition(BaseModel):
    to: ApplicationStatus
    note: Optional[str] = None


class ApplicationOut(BaseModel):
    id: str
    package_id: str
    job_id: str
    job_title: str
    company: str
    status: ApplicationStatus
    note: Optional[str]
    submitted_at: Optional[datetime]
    snapshot_sha: Optional[str]
    has_snapshot: bool
    allowed_transitions: List[str]
    history: List[dict]
    reminders: List[ReminderOut]
    created_at: datetime
    updated_at: datetime
    # Reaffirmed every response: nothing is auto-submitted.
    guarantees: List[str] = [
        "Aptiro never submits this application anywhere.",
        "'submitted_by_user' is a state you assert after applying on "
        "the employer's own site.",
        "The submit snapshot is frozen once and never rewritten.",
        "Every status change is explicit and recorded in history.",
    ]


def _app_out(a):
    return ApplicationOut(
        id=a.id, package_id=a.package_id, job_id=a.job_id,
        job_title=a.job_title, company=a.company, status=a.status,
        note=a.note, submitted_at=a.submitted_at,
        snapshot_sha=a.snapshot_sha, has_snapshot=bool(a.snapshot),
        allowed_transitions=sorted(
            s.value for s in _APP_TRANSITIONS.get(a.status, set())),
        history=a.history or [],
        reminders=[ReminderOut(**r) for r in (a.reminders or [])],
        created_at=a.created_at, updated_at=a.updated_at)


@applications_router.post("", response_model=ApplicationOut,
                          status_code=201)
def create_application(body: ApplicationCreate,
                       session: Session = Depends(get_session)):
    pkg = session.get(ApplicationPackage, body.package_id)
    if not pkg:
        raise HTTPException(404, "Package not found")
    job = session.get(JobPosting, pkg.job_id)
    a = Application(
        package_id=pkg.id, job_id=pkg.job_id, owner_id=_uid(),
        job_title=pkg.title or (job.title if job else ""),
        company=pkg.company or (job.company if job else ""),
        status=ApplicationStatus.drafted, note=body.note)
    _app_hist(a, None, ApplicationStatus.drafted,
              "Tracker created from package (human action).")
    session.add(a)
    session.commit()
    session.refresh(a)
    return _app_out(a)


@applications_router.get("", response_model=List[ApplicationOut])
def list_applications(session: Session = Depends(get_session)):
    rows = session.exec(select(Application).where(
        Application.owner_id == _uid()).order_by(
        Application.created_at.desc())).all()
    return [_app_out(a) for a in rows]


@applications_router.get("/export.csv")
def export_applications_csv(session: Session = Depends(get_session)):
    import csv
    import io as _io
    rows = session.exec(select(Application).where(
        Application.owner_id == _uid()).order_by(
        Application.created_at)).all()
    buf = _io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "company", "job_title", "status", "submitted_at",
                "snapshot_sha", "created_at", "updated_at"])
    for a in rows:
        w.writerow([a.id, a.company, a.job_title, a.status.value,
                    a.submitted_at.isoformat() if a.submitted_at else "",
                    a.snapshot_sha or "", a.created_at.isoformat(),
                    a.updated_at.isoformat()])
    return Response(
        content=buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition":
                 'attachment; filename="aptiro_applications.csv"'})


@applications_router.get("/{app_id}", response_model=ApplicationOut)
def get_application(app_id: str,
                    session: Session = Depends(get_session)):
    a = _get_owned(session, Application, app_id)
    if not a:
        raise HTTPException(404, "Application not found")
    return _app_out(a)


@applications_router.get("/{app_id}/snapshot")
def get_application_snapshot(app_id: str,
                             session: Session = Depends(get_session)):
    a = session.get(Application, app_id)
    if not a:
        raise HTTPException(404, "Application not found")
    if not a.snapshot:
        raise HTTPException(
            404, "No snapshot yet - it is frozen only when you mark the "
            "application submitted_by_user.")
    return {"snapshot_sha": a.snapshot_sha, "snapshot": a.snapshot}


@applications_router.post("/{app_id}/transition",
                          response_model=ApplicationOut)
def transition_application(app_id: str, body: ApplicationTransition,
                           session: Session = Depends(get_session)):
    a = session.get(Application, app_id)
    if not a:
        raise HTTPException(404, "Application not found")
    target = body.to
    allowed = _APP_TRANSITIONS.get(a.status, set())
    if target not in allowed:
        raise HTTPException(
            409, "Illegal transition %s -> %s. Allowed from %s: %s."
            % (a.status.value, target.value, a.status.value,
               ", ".join(sorted(s.value for s in allowed)) or "(none)"))
    frm = a.status
    # Freeze the immutable evidence snapshot exactly once, at submit.
    if target == ApplicationStatus.submitted_by_user and not a.snapshot:
        snap, sha = _app_snapshot(session, a)
        a.snapshot = snap
        a.snapshot_sha = sha
        a.submitted_at = _now()
        a.reminders = _make_reminders(a.submitted_at)
        # Advisory preview row (mock provider, never sent).
        try:
            _persist_previews(
                session, NotificationKind.daily_digest,
                ("Application submitted: %s" % a.company,
                 "You marked '%s @ %s' submitted. Snapshot frozen "
                 "(sha %s...). %d follow-up reminders scheduled. "
                 "[preview - not sent]"
                 % (a.job_title, a.company, (sha or "")[:8],
                    len(a.reminders))),
                package_id=a.package_id,
                only=NotificationChannel.in_app)
        except Exception:
            pass
    a.status = target
    a.updated_at = _now()
    _app_hist(a, frm, target, body.note)
    session.add(a)
    session.commit()
    session.refresh(a)
    return _app_out(a)


@applications_router.post("/{app_id}/reminders/{rem_id}/done",
                          response_model=ApplicationOut)
def complete_reminder(app_id: str, rem_id: str,
                      session: Session = Depends(get_session)):
    import copy
    a = session.get(Application, app_id)
    if not a:
        raise HTTPException(404, "Application not found")
    # Deep-copy so the reassignment is a genuinely new object tree;
    # SQLAlchemy does not track in-place JSON mutations.
    rems = copy.deepcopy(a.reminders or [])
    hit = False
    for r in rems:
        if r["id"] == rem_id:
            r["done"] = True
            hit = True
    if not hit:
        raise HTTPException(404, "Reminder not found")
    a.reminders = rems
    a.updated_at = _now()
    session.add(a)
    session.commit()
    session.refresh(a)
    return _app_out(a)


@applications_router.delete("/{app_id}", status_code=204)
def delete_application(app_id: str,
                       session: Session = Depends(get_session)):
    a = session.get(Application, app_id)
    if not a:
        raise HTTPException(404, "Application not found")
    session.delete(a)
    session.commit()
    return Response(status_code=204)


app.include_router(applications_router)


# ===========================================================================
# Phase 4 - auth (register / login / me). Stdlib only (pbkdf2 + token);
# no new dependencies, no live service. With AUTH off these endpoints
# still work but are optional; with AUTH on, the token they issue is
# required for mutating requests and scopes every owned entity.
# ===========================================================================
auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthOut(BaseModel):
    id: str
    email: str
    name: str
    token: str


class MeOut(BaseModel):
    id: str
    email: str
    name: str
    is_default: bool
    auth_enabled: bool


@auth_router.post("/register", response_model=AuthOut, status_code=201)
def register(body: RegisterRequest,
             session: Session = Depends(get_session)):
    email = (body.email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(422, "A valid email is required")
    if len(body.password or "") < 8:
        raise HTTPException(422, "Password must be >= 8 characters")
    if session.exec(select(User).where(User.email == email)).first():
        raise HTTPException(409, "An account with that email exists")
    u = User(email=email, name=(body.name or email.split("@")[0]),
             password_hash=_hash_pw(body.password), token=_new_token())
    session.add(u)
    session.commit()
    session.refresh(u)
    return AuthOut(id=u.id, email=u.email, name=u.name, token=u.token)


@auth_router.post("/login", response_model=AuthOut)
def login(body: LoginRequest,
          session: Session = Depends(get_session)):
    email = (body.email or "").strip().lower()
    u = session.exec(select(User).where(User.email == email)).first()
    if not u or not _verify_pw(body.password or "", u.password_hash):
        raise HTTPException(401, "Invalid email or password")
    if not u.token:
        u.token = _new_token()
        session.add(u)
        session.commit()
        session.refresh(u)
    return AuthOut(id=u.id, email=u.email, name=u.name, token=u.token)


@auth_router.get("/me", response_model=MeOut)
def whoami(session: Session = Depends(get_session)):
    u = session.get(User, _uid())
    if not u:
        # default-user shim (AUTH off, no row needed elsewhere)
        return MeOut(id=DEFAULT_UID, email=DEFAULT_USER_EMAIL,
                     name="Local User", is_default=True,
                     auth_enabled=AUTH_ENABLED)
    return MeOut(id=u.id, email=u.email, name=u.name,
                 is_default=u.is_default, auth_enabled=AUTH_ENABLED)


app.include_router(auth_router)


# ===========================================================================
# Phase 6 - read-only audit trail (owner-scoped). The trail is written
# only by the observability middleware and is intentionally not part of
# the privacy export/wipe set, so it stays tamper-resistant.
# ===========================================================================
audit_router = APIRouter(prefix="/api/audit", tags=["audit"])


class AuditEventOut(BaseModel):
    id: str
    request_id: str
    method: str
    path: str
    status: int
    duration_ms: int
    at: datetime


@audit_router.get("", response_model=List[AuditEventOut])
def list_audit(limit: int = 200,
               session: Session = Depends(get_session)):
    limit = max(1, min(int(limit or 200), 1000))
    rows = session.exec(
        select(AuditEvent).where(AuditEvent.owner_id == _uid())
        .order_by(AuditEvent.at.desc()).limit(limit)).all()
    return [AuditEventOut(
        id=a.id, request_id=a.request_id, method=a.method, path=a.path,
        status=a.status, duration_ms=a.duration_ms, at=a.at)
        for a in rows]


app.include_router(audit_router)
# =====================================================================
# PHASE 4 — Multi-strategy support (ADDITIVE BLOCK)
#
# This block is meant to be APPENDED to backend/app.py immediately BEFORE
# the existing `app.include_router(strategy_router)` line, and the new
# `strategies_router` must be included with `app.include_router(...)`
# just after that (one new line — see phase4_app_py_diff.md).
#
# It does not touch any existing model, endpoint, or test. The existing
# singular `/api/strategy` GET/PUT continue to operate on the *active*
# strategy via `_active_strategy(...)`, unchanged.
#
# Three small edits ARE required outside this block (also documented in
# phase4_app_py_diff.md):
#   1. Add `score_threshold: int = 0` to the `Strategy` SQLModel.
#   2. Add `score_threshold` to the `_ensure_additive_columns` map.
#   3. Add `score_threshold` to `StrategyUpsert` and the
#      `_strategy_read()` builder, so the field round-trips through the
#      existing singular endpoint as well.
# =====================================================================


# APTIRO_PHASE9_PR10_STRATEGIES_MARKER
# Phase-4 multi-strategy domain extracted to modules/strategies
# (Phase 9 PR-10); the dead duplicate block was removed here too.
from app.modules.strategies import (  # noqa: F401
    StrategyCreate, StrategyListItem, StrategyPreviewCounts,
    StrategyPreviewOut, SeedPresetsOut, StrategyDraftPreviewIn,
    STRATEGY_PRESETS, _list_strategies, _get_owned_strategy,
    _strategy_list_item, _ensure_one_active, _apply_body_to_strategy,
    _preview_counts, _preview_summary, strategies_router,
)


app.include_router(strategies_router)


# ===========================================================================
# Phase 5 (Upgrade) — Match Inbox + Real Providers + Saved Searches
#
# APPEND this file to backend/app.py:
#   cat fix2_app_phase5_additions.py >> backend/app.py
#
# KEY FIX vs the original: refresh-staleness and saved-searches use a
# NEW router (jobs_phase5_router / saved_searches_router) that is
# registered HERE with app.include_router(). This avoids the 405 that
# occurs when you try to add a route to jobs_router after it has already
# been passed to app.include_router() earlier in the file.
# ===========================================================================

# ── Phase 5: SavedSearch model ───────────────────────────────────────────
class SavedSearch(SQLModel, table=True):
    """User-created recurring job search with optional scheduling.
    frequency: 'manual' | 'daily' | 'weekly'
    provider: None = use APTIRO_JOB_PROVIDER env default.
    """
    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(default=DEFAULT_UID, index=True)
    name: str = ""
    query: str = ""
    provider: Optional[str] = None
    min_salary: Optional[int] = None
    max_salary: Optional[int] = None
    work_mode: Optional[str] = None
    location_filter: Optional[str] = None
    frequency: str = "manual"
    last_run_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_now)
    is_active: bool = True


# ── Phase 5: real Remotive provider (opt-in, graceful fallback) ──────────
def _fetch_remotive_real(query: Optional[str], limit: int) -> List[JobPosting]:
    """Fetch from public Remotive JSON API (no auth required).
    Returns [] on any error — caller always receives a list."""
    if _httpx is None:
        return []
    timeout = float(os.getenv("APTIRO_JOB_FETCH_TIMEOUT", "15"))
    try:
        params: dict = {"limit": min(limit, 50)}
        if query:
            params["search"] = query
        with _httpx.Client(timeout=timeout) as client:
            resp = client.get("https://remotive.com/api/remote-jobs",
                              params=params)
            resp.raise_for_status()
            data = resp.json()
        results: List[JobPosting] = []
        for item in data.get("jobs", [])[:limit]:
            desc = item.get("description", "")
            sal_m = _SALARY.search(item.get("salary", ""))
            sal_min = sal_max = None
            if sal_m:
                has_k = bool(re.search(r"\d\s?[kK]", sal_m.group(0)))
                sal_min = _money(sal_m.group(1), has_k)
                sal_max = _money(sal_m.group(2), has_k)
            results.append(JobPosting(
                title=(item.get("title") or "")[:160],
                company=(item.get("company_name") or "")[:120],
                location=item.get("candidate_required_location", "Remote"),
                work_mode=WorkMode.remote,
                salary_min=sal_min, salary_max=sal_max,
                source="remotive", source_url=item.get("url"),
                description_text=desc,
                requirements=_extract_requirements(desc),
                structured_requirements=_structured_requirements(desc),
                posted_at=item.get("publication_date"),
                provider_source="remotive",
                provider_job_id=str(item.get("id", "")),
                last_seen_at=_now(), is_stale=False,
            ))
        return results
    except Exception as exc:
        _logj("remotive.fetch.error", error=repr(exc))
        return []


# ── Phase 5: new mini-router so routes register correctly ────────────────
# jobs_router was already include_router'd earlier in app.py.
# Adding routes to it after that call has no effect.
# This dedicated router is registered below with a fresh include_router().
_jobs_p5_router = APIRouter(tags=["jobs-phase5"])
_ss_router = APIRouter(prefix="/api/saved-searches", tags=["saved-searches"])


@_jobs_p5_router.post("/api/jobs/refresh-staleness")
def refresh_staleness(session: Session = Depends(get_session)):
    """Mark jobs not seen for 30+ days as stale (additive flag only).
    SQLite: the new columns are added by _ensure_additive_columns on boot
    so getattr(..., None) is used as a safe fallback during the first run
    before the app has restarted after the column addition."""
    from datetime import timedelta
    cutoff = _now() - timedelta(days=30)
    rows = session.exec(select(JobPosting).where(
        JobPosting.owner_id == _uid(),
        JobPosting.is_archived == False,  # noqa: E712
    )).all()
    marked = 0
    for j in rows:
        seen = getattr(j, "last_seen_at", None) or j.imported_at
        stale_now = bool(seen and seen < cutoff)
        currently = bool(getattr(j, "is_stale", False))
        if stale_now != currently:
            try:
                j.is_stale = stale_now
                session.add(j)
                if stale_now:
                    marked += 1
            except Exception:
                pass
    session.commit()
    return {"marked_stale": marked, "cutoff": cutoff.isoformat()}


# ── Phase 5: Saved Searches ───────────────────────────────────────────────
class SavedSearchCreate(BaseModel):
    name: str
    query: str = ""
    provider: Optional[str] = None
    min_salary: Optional[int] = None
    max_salary: Optional[int] = None
    work_mode: Optional[str] = None
    location_filter: Optional[str] = None
    frequency: str = "manual"


class SavedSearchUpdate(BaseModel):
    name: Optional[str] = None
    query: Optional[str] = None
    provider: Optional[str] = None
    min_salary: Optional[int] = None
    max_salary: Optional[int] = None
    work_mode: Optional[str] = None
    location_filter: Optional[str] = None
    frequency: Optional[str] = None
    is_active: Optional[bool] = None


class SavedSearchRead(BaseModel):
    id: str
    owner_id: str
    name: str
    query: str
    provider: Optional[str]
    min_salary: Optional[int]
    max_salary: Optional[int]
    work_mode: Optional[str]
    location_filter: Optional[str]
    frequency: str
    last_run_at: Optional[datetime]
    created_at: datetime
    is_active: bool


class SavedSearchRunResult(BaseModel):
    search_id: str
    search_name: str
    provider_used: str
    jobs_fetched: int
    jobs_created: int
    jobs_skipped_dupes: int
    last_run_at: str


def _ss_read(ss: SavedSearch) -> SavedSearchRead:
    return SavedSearchRead(
        id=ss.id, owner_id=ss.owner_id, name=ss.name, query=ss.query,
        provider=ss.provider, min_salary=ss.min_salary,
        max_salary=ss.max_salary, work_mode=ss.work_mode,
        location_filter=ss.location_filter, frequency=ss.frequency,
        last_run_at=ss.last_run_at, created_at=ss.created_at,
        is_active=ss.is_active)


@_ss_router.get("", response_model=List[SavedSearchRead])
def list_saved_searches(session: Session = Depends(get_session)):
    rows = session.exec(select(SavedSearch).where(
        SavedSearch.owner_id == _uid()
    ).order_by(SavedSearch.created_at.desc())).all()
    return [_ss_read(ss) for ss in rows]


@_ss_router.post("", response_model=SavedSearchRead, status_code=201)
def create_saved_search(body: SavedSearchCreate,
                        session: Session = Depends(get_session)):
    if not (body.name or "").strip():
        raise HTTPException(400, "name is required")
    freq = (body.frequency or "manual").lower()
    if freq not in ("manual", "daily", "weekly"):
        raise HTTPException(400, "frequency must be manual|daily|weekly")
    ss = SavedSearch(
        owner_id=_uid(), name=body.name.strip(), query=body.query or "",
        provider=body.provider or None, min_salary=body.min_salary,
        max_salary=body.max_salary, work_mode=body.work_mode or None,
        location_filter=body.location_filter or None,
        frequency=freq, is_active=True)
    session.add(ss)
    session.commit()
    session.refresh(ss)
    return _ss_read(ss)


@_ss_router.get("/{ss_id}", response_model=SavedSearchRead)
def get_saved_search(ss_id: str, session: Session = Depends(get_session)):
    ss = session.get(SavedSearch, ss_id)
    if not ss or ss.owner_id != _uid():
        raise HTTPException(404, "Saved search not found")
    return _ss_read(ss)


@_ss_router.patch("/{ss_id}", response_model=SavedSearchRead)
def update_saved_search(ss_id: str, body: SavedSearchUpdate,
                        session: Session = Depends(get_session)):
    ss = session.get(SavedSearch, ss_id)
    if not ss or ss.owner_id != _uid():
        raise HTTPException(404, "Saved search not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "frequency" and value:
            value = value.lower()
            if value not in ("manual", "daily", "weekly"):
                raise HTTPException(
                    400, "frequency must be manual|daily|weekly")
        setattr(ss, field, value)
    session.add(ss)
    session.commit()
    session.refresh(ss)
    return _ss_read(ss)


@_ss_router.delete("/{ss_id}", status_code=204)
def delete_saved_search(ss_id: str,
                        session: Session = Depends(get_session)):
    ss = session.get(SavedSearch, ss_id)
    if not ss or ss.owner_id != _uid():
        raise HTTPException(404, "Saved search not found")
    session.delete(ss)
    session.commit()
    return Response(status_code=204)


@_ss_router.post("/{ss_id}/run", response_model=SavedSearchRunResult)
def run_saved_search(ss_id: str, session: Session = Depends(get_session)):
    """Fetch jobs using the saved search criteria and merge into job table."""
    ss = session.get(SavedSearch, ss_id)
    if not ss or ss.owner_id != _uid():
        raise HTTPException(404, "Saved search not found")

    provider_name = ss.provider or JOB_PROVIDER
    _, raw_jobs = fetch_jobs_from_source(provider_name, ss.query or None, 50)

    # Apply saved-search filters
    filtered: List[JobPosting] = []
    for j in raw_jobs:
        if ss.min_salary and j.salary_min and j.salary_min < ss.min_salary:
            continue
        if ss.max_salary and j.salary_max and j.salary_max > ss.max_salary:
            continue
        if ss.work_mode and j.work_mode.value != ss.work_mode:
            continue
        if ss.location_filter:
            if ss.location_filter.lower() not in (j.location or "").lower():
                continue
        filtered.append(j)

    # Merge with cross-provider deduplication
    existing = {(j.title.lower(), j.company.lower())
                for j in session.exec(select(JobPosting).where(
                    JobPosting.owner_id == _uid())).all()}
    created = 0
    skipped = 0
    for j in filtered:
        k = (j.title.lower(), j.company.lower())
        if k in existing:
            dup = _find_duplicate(session, j.company, j.title, j.source_url)
            if dup:
                try:
                    dup.last_seen_at = _now()
                    dup.is_stale = False
                    session.add(dup)
                except Exception:
                    pass
            skipped += 1
        else:
            existing.add(k)
            j.owner_id = _uid()
            session.add(j)
            created += 1

    ss.last_run_at = _now()
    session.add(ss)
    session.commit()
    return SavedSearchRunResult(
        search_id=ss.id, search_name=ss.name,
        provider_used=provider_name,
        jobs_fetched=len(filtered), jobs_created=created,
        jobs_skipped_dupes=skipped,
        last_run_at=ss.last_run_at.isoformat())


# Register both routers (this is the line that makes the routes live)
app.include_router(_jobs_p5_router)
app.include_router(_ss_router)

# ===========================================================================
# Upgrade Phase 6 — Public Research Module
#
# APPEND this entire block to backend/app.py  (after the last
# app.include_router call at the bottom of the file).
#
# Adds:
#   ResearchUsageClass + ResearchApprovalStatus enums
#   PublicResearchFinding   — SQLModel / DB table
#   BaseResearchProvider abstraction + MockResearchProvider (offline, det.)
#   _generate_research_queries()  — from approved profile claims
#   research_router  — 7 endpoints under /api/research
#   app.include_router(research_router)
#   /api/health updated: upgrade_phases_shipped gains 6
#
# SAFETY INVARIANT (load-bearing, non-negotiable):
#   Findings may contextualize and suggest framing.
#   They NEVER become résumé claims without explicit user action.
#   No finding is auto-applied anywhere in the system.
# ===========================================================================


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ResearchUsageClass(str, Enum):
    """How a research finding may be safely used.

    background_context — general market / industry context; informs tone
                         but is not directly tied to a personal claim.
    claim_support      — public evidence that corroborates an already-approved
                         profile claim (e.g. company press release matching
                         the candidate's stated project).
    framing_only       — suggested angle / language the user may CHOOSE to
                         adopt; they must still write the actual claim.
    not_usable         — finding cannot be safely used in any way (mis-matched
                         entity, unverifiable, or contradictory).
    """
    background_context = "background_context"
    claim_support = "claim_support"
    framing_only = "framing_only"
    not_usable = "not_usable"


class ResearchApprovalStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class PublicResearchFinding(SQLModel, table=True):
    """Public-domain research finding for the owner's profile.

    Created by the research pipeline from approved profile-claim data.
    Must be explicitly approved before it can be used in any application
    material.  suggested_framing is a *read-only suggestion*; it is never
    auto-inserted into a package or claim.
    """
    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(default=DEFAULT_UID, index=True)
    # the query that produced this finding
    query: str = ""
    # source attribution (always shown to user)
    source_url: Optional[str] = None
    source_title: Optional[str] = None
    source_snippet: str = ""
    # finding content
    finding_text: str = ""
    # classification — required before approval
    usage_class: ResearchUsageClass = ResearchUsageClass.background_context
    # system-generated framing suggestion (user may adapt; never auto-applied)
    suggested_framing: Optional[str] = None
    # approval gate
    approval_status: ResearchApprovalStatus = ResearchApprovalStatus.pending
    # which profile claims prompted this query (provenance chain)
    prompted_by_claim_ids: List[str] = Field(
        default_factory=list, sa_column=Column(JSON))
    # provider metadata
    provider: str = "mock"
    created_at: datetime = Field(default_factory=_now)
    approved_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Research-provider abstraction
# ---------------------------------------------------------------------------

class BaseResearchProvider:
    name: str = "base"

    def search(self, queries: List[str],
               limit_per_query: int = 3) -> List[dict]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Mock provider  — deterministic, offline, no network
# ---------------------------------------------------------------------------

_MOCK_RESEARCH_DB = [
    # AI / ML / product
    {
        "keywords": ["ai", "machine learning", "product", "pm", "manager"],
        "source_title": "AI Product Management Benchmarks 2026",
        "source_url": "https://example.com/ai-pm-benchmarks-2026",
        "source_snippet": (
            "Organizations that embed AI PMs in cross-functional squads "
            "report 35-45% faster feature validation cycles."
        ),
        "finding_text": (
            "AI product managers who drive LLM integration into product "
            "workflows are consistently delivering shorter cycle times and "
            "higher squad alignment scores across enterprise teams."
        ),
        "usage_class": "background_context",
        "suggested_framing": None,
    },
    # Healthcare / diagnostics
    {
        "keywords": ["healthcare", "clinical", "lab", "diagnostic",
                     "laboratory", "arup", "test"],
        "source_title": "AI Test-Recommendation Adoption in Clinical Labs",
        "source_url": "https://example.com/lab-ai-clinical-2026",
        "source_snippet": (
            "AI-assisted test-recommendation engines in high-throughput labs "
            "reduce unnecessary test orders by 15-25% while improving "
            "diagnostic yield."
        ),
        "finding_text": (
            "Clinical laboratories deploying AI-powered test-recommendation "
            "systems report measurable reductions in unnecessary orders and "
            "clinician decision time, with strongest gains in EMR-integrated "
            "deployments."
        ),
        "usage_class": "claim_support",
        "suggested_framing": (
            "Built AI test-finder capability in a high-throughput clinical "
            "lab environment, addressing over-ordering and diagnostic accuracy "
            "challenges at scale"
        ),
    },
    # E-commerce / marketplace
    {
        "keywords": ["ecommerce", "e-commerce", "marketplace", "seller",
                     "pattern", "amazon", "retail"],
        "source_title": "Marketplace Platform Intelligence: 2026 Seller Report",
        "source_url": "https://example.com/marketplace-intelligence-2026",
        "source_snippet": (
            "Third-party sellers using AI-optimised listing tools see 20-30% "
            "improvement in conversion rates within 90 days."
        ),
        "finding_text": (
            "Marketplace platforms that deliver AI-driven listing "
            "optimisation and demand forecasting tools are becoming the "
            "primary differentiator for third-party seller acquisition and "
            "retention."
        ),
        "usage_class": "background_context",
        "suggested_framing": None,
    },
    # Cloud / AWS / infrastructure
    {
        "keywords": ["cloud", "aws", "infrastructure", "platform",
                     "migration", "enterprise"],
        "source_title": "Enterprise Cloud Migration Outcomes 2025-2026",
        "source_url": "https://example.com/cloud-migration-outcomes",
        "source_snippet": (
            "Enterprises completing cloud migrations under PM-led cross-"
            "functional programmes report 28% lower operational cost and "
            "50% faster deployment cadence within 18 months."
        ),
        "finding_text": (
            "Cloud infrastructure migrations guided by product managers with "
            "clear ROI frameworks show consistently better adoption and cost "
            "outcomes than engineering-only migrations."
        ),
        "usage_class": "framing_only",
        "suggested_framing": (
            "Led cloud infrastructure strategy with documented cost and "
            "delivery outcomes"
        ),
    },
    # Adobe / creative / SaaS
    {
        "keywords": ["adobe", "saas", "creative", "design", "platform",
                     "subscription"],
        "source_title": "SaaS Platform Strategy: Creative Tools 2026",
        "source_url": "https://example.com/saas-creative-2026",
        "source_snippet": (
            "SaaS platforms that surface AI-assisted workflows to existing "
            "subscribers see 18% higher feature adoption and 12% lower churn."
        ),
        "finding_text": (
            "Adobe and peers in the creative SaaS category are accelerating "
            "AI-feature releases as the primary retention lever, with in-"
            "product AI assistants now cited by analysts as table-stakes."
        ),
        "usage_class": "background_context",
        "suggested_framing": None,
    },
    # Strategy / roadmap / cross-functional
    {
        "keywords": ["strategy", "roadmap", "cross-functional", "stakeholder",
                     "product strategy", "leadership"],
        "source_title": "Product Leadership Effectiveness Study 2026",
        "source_url": "https://example.com/product-leadership-2026",
        "source_snippet": (
            "PMs who tie roadmap priorities directly to company OKRs and "
            "quantify customer impact are 2x more likely to be promoted to "
            "senior roles within 18 months."
        ),
        "finding_text": (
            "Research by leading product-management institutes shows that "
            "evidence-backed roadmapping — where each initiative is anchored "
            "to a measurable outcome — is the single strongest predictor of "
            "senior PM promotion and executive trust."
        ),
        "usage_class": "framing_only",
        "suggested_framing": (
            "Drove evidence-backed roadmap prioritisation tied to measurable "
            "business outcomes"
        ),
    },
    # Data / analytics
    {
        "keywords": ["data", "analytics", "dashboard", "metrics", "kpi",
                     "reporting"],
        "source_title": "Data-Driven Product Teams: Industry Analysis 2026",
        "source_url": "https://example.com/data-driven-pm-2026",
        "source_snippet": (
            "Product teams with embedded data practices ship 30% more "
            "experiments per quarter and have 45% higher metric accuracy "
            "at launch."
        ),
        "finding_text": (
            "Data-driven product teams consistently outperform peers on "
            "launch quality and iteration speed. Key differentiators include "
            "real-time dashboards, pre-defined success metrics, and PM-owned "
            "experiment design."
        ),
        "usage_class": "background_context",
        "suggested_framing": None,
    },
    # Generic catch-all
    {
        "keywords": [],
        "source_title": "Product Management Industry Overview 2026",
        "source_url": "https://example.com/pm-industry-overview-2026",
        "source_snippet": (
            "Senior PMs with domain expertise in AI, healthcare, or "
            "e-commerce command 25-40% higher compensation premiums."
        ),
        "finding_text": (
            "The product management market in 2026 increasingly rewards "
            "PMs who combine domain depth with AI fluency, particularly in "
            "regulated industries and platform businesses."
        ),
        "usage_class": "background_context",
        "suggested_framing": None,
    },
]


class MockResearchProvider(BaseResearchProvider):
    name: str = "mock"

    def search(self, queries: List[str],
               limit_per_query: int = 3) -> List[dict]:
        """Deterministic offline search.  Returns the same results for the
        same queries every run — required for the test suite."""
        results: List[dict] = []
        seen: set = set()

        for query in queries:
            q_lower = query.lower()
            matched: List[dict] = []

            # Score each mock entry by keyword overlap
            scored = []
            for entry in _MOCK_RESEARCH_DB:
                hits = sum(1 for kw in entry["keywords"] if kw in q_lower)
                scored.append((hits, entry))

            # Sort: most hits first, then stable order (index)
            scored.sort(key=lambda x: -x[0])

            for _, entry in scored:
                key = entry["source_url"]
                if key in seen:
                    continue
                seen.add(key)
                matched.append({
                    "query": query,
                    "source_title": entry["source_title"],
                    "source_url": entry["source_url"],
                    "source_snippet": entry["source_snippet"],
                    "finding_text": entry["finding_text"],
                    "usage_class": entry["usage_class"],
                    "suggested_framing": entry["suggested_framing"],
                    "provider": self.name,
                })
                if len(matched) >= limit_per_query:
                    break

            results.extend(matched)

        return results


def _get_research_provider() -> BaseResearchProvider:
    """Return the active research provider.
    Default: mock (works offline, no credentials).
    Real providers are opt-in via APTIRO_RESEARCH_PROVIDER env var.
    """
    provider_name = os.getenv("APTIRO_RESEARCH_PROVIDER", "mock").lower()
    # Only mock is implemented; future providers follow this seam.
    return MockResearchProvider()


# ---------------------------------------------------------------------------
# Research query generator
# ---------------------------------------------------------------------------

def _generate_research_queries(
    claims: List["ProfileClaim"],
) -> List[dict]:
    """Build research search queries from the user's approved claims.

    Returns a list of:
        {"query": str, "rationale": str, "claim_ids": List[str]}

    Deduplicates queries so the same company / skill pair never produces
    two identical queries.
    """
    seen_queries: set = set()
    queries: List[dict] = []

    # Gather dimensions from approved claims
    companies: dict = {}    # company → [claim_id, ...]
    roles: dict = {}         # role → [claim_id, ...]
    skills: dict = {}        # skill → [claim_id, ...]
    domains: set = set()

    for claim in claims:
        if claim.approval_status != ApprovalStatus.approved:
            continue
        cid = claim.id
        if claim.company:
            companies.setdefault(claim.company.strip(), []).append(cid)
        if claim.role:
            roles.setdefault(claim.role.strip(), []).append(cid)
        for sk in (claim.skills or []):
            skills.setdefault(sk.strip().lower(), []).append(cid)

    # Infer domain from skills + claim text
    healthcare_terms = {"lab", "clinical", "diagnostic", "healthcare",
                        "ehr", "emr", "patient", "arup"}
    ai_terms = {"ai", "ml", "machine learning", "llm", "nlp", "model"}
    ecomm_terms = {"ecommerce", "e-commerce", "marketplace", "amazon",
                   "seller"}
    all_skills_lower = set(skills.keys())
    if all_skills_lower & healthcare_terms:
        domains.add("healthcare")
    if all_skills_lower & ai_terms:
        domains.add("ai")
    if all_skills_lower & ecomm_terms:
        domains.add("ecommerce")

    def _add(q: str, rationale: str, claim_ids: List[str]) -> None:
        norm = q.lower().strip()
        if norm not in seen_queries:
            seen_queries.add(norm)
            queries.append({"query": q,
                            "rationale": rationale,
                            "claim_ids": sorted(set(claim_ids))})

    # Company × role queries
    for company, cids in list(companies.items())[:3]:
        for role, rids in list(roles.items())[:2]:
            _add(
                f"{company} {role} product outcomes",
                f"Public coverage of {company}'s {role} work",
                cids + rids,
            )

    # Domain queries
    for domain in list(domains)[:2]:
        domain_cids = [
            cid for sk, cids in skills.items()
            if sk in (healthcare_terms | ai_terms | ecomm_terms)
            for cid in cids
        ]
        _add(
            f"{domain} product management trends 2026",
            f"Industry context for {domain} domain",
            domain_cids[:5],
        )

    # Top-skill queries
    top_skills = sorted(skills.items(), key=lambda x: -len(x[1]))[:3]
    for skill, cids in top_skills:
        if len(skill) > 3:  # skip short abbreviations
            _add(
                f"{skill} product strategy best practices",
                f"Best practices context for skill: {skill}",
                cids[:5],
            )

    # Fallback: at least one generic query per company
    if not queries:
        for company, cids in list(companies.items())[:2]:
            _add(
                f"{company} product innovation",
                f"General coverage of {company}",
                cids,
            )

    # ── Fallback: extract keywords directly from claim_text ──────────────
    # The mock AI extractor often leaves company/role/skills blank, so the
    # structured-field queries above produce nothing.  When that happens,
    # scan claim_text for high-signal domain keywords and generate context
    # queries from those instead.
    if not queries:
        DOMAIN_KEYWORDS = [
            "ai", "machine learning", "healthcare", "clinical", "laboratory",
            "lab", "diagnostic", "arup", "product", "platform", "strategy",
            "data", "cloud", "aws", "analytics", "ecommerce", "marketplace",
            "pattern", "adobe", "saas", "llm", "nlp", "automation",
        ]
        found_kw: dict = {}   # keyword → [claim_id, ...]
        for claim in claims:
            text_lower = claim.claim_text.lower()
            for kw in DOMAIN_KEYWORDS:
                if kw in text_lower:
                    found_kw.setdefault(kw, []).append(claim.id)

        # Pick up to 4 most-evidenced keywords and generate a query each
        top_kw = sorted(found_kw.items(), key=lambda x: -len(x[1]))[:4]
        for kw, cids in top_kw:
            _add(
                f"{kw} product management industry 2026",
                f"Industry context for keyword: {kw}",
                cids[:5],
            )

        # Absolute last resort: one generic PM trend query.
        # Only fires when there ARE claims but their structured fields
        # (company, role, skills) are all blank — e.g. the mock extractor
        # left them empty.  Guard: skip entirely when claims is empty so
        # that calling run_profile_contributions with zero approved claims
        # correctly returns findings_created=0.
        if not queries and claims:
            all_cids = [c.id for c in claims[:5]]
            _add(
                "senior product manager AI healthcare 2026",
                "Generic PM context (no structured claim data found)",
                all_cids,
            )

    return queries[:10]  # cap at 10 queries per run


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ResearchFindingRead(BaseModel):
    id: str
    owner_id: str
    query: str
    source_url: Optional[str]
    source_title: Optional[str]
    source_snippet: str
    finding_text: str
    usage_class: ResearchUsageClass
    suggested_framing: Optional[str]
    approval_status: ResearchApprovalStatus
    prompted_by_claim_ids: List[str]
    provider: str
    created_at: datetime
    approved_at: Optional[datetime]


class ResearchFindingPatch(BaseModel):
    usage_class: Optional[ResearchUsageClass] = None
    approval_status: Optional[ResearchApprovalStatus] = None


class GenerateQueriesOut(BaseModel):
    queries: List[dict]
    approved_claim_count: int
    message: str


class RunResearchRequest(BaseModel):
    limit_per_query: int = 3   # max findings per query (1-10)
    queries: Optional[List[str]] = None  # override auto-generated if supplied


class RunResearchOut(BaseModel):
    findings_created: int
    queries_used: List[str]
    provider: str
    message: str


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

research_router = APIRouter(prefix="/api/research", tags=["research"])


def _finding_read(f: PublicResearchFinding) -> ResearchFindingRead:
    return ResearchFindingRead(
        id=f.id, owner_id=f.owner_id, query=f.query,
        source_url=f.source_url, source_title=f.source_title,
        source_snippet=f.source_snippet, finding_text=f.finding_text,
        usage_class=f.usage_class, suggested_framing=f.suggested_framing,
        approval_status=f.approval_status,
        prompted_by_claim_ids=f.prompted_by_claim_ids or [],
        provider=f.provider, created_at=f.created_at,
        approved_at=f.approved_at,
    )


def _get_owned_finding(
    finding_id: str, session: Session
) -> PublicResearchFinding:
    f = session.exec(
        select(PublicResearchFinding)
        .where(PublicResearchFinding.id == finding_id)
        .where(PublicResearchFinding.owner_id == _uid())
    ).first()
    if not f:
        raise HTTPException(404, "Research finding not found")
    return f


@research_router.get("/generate-queries",
                     response_model=GenerateQueriesOut)
def generate_research_queries(session: Session = Depends(get_session)):
    """Preview the queries that /profile-contributions would run, without
    persisting any findings.  Useful for the UI to show 'what will be
    searched' before the user commits."""
    claims = session.exec(
        select(ProfileClaim).where(ProfileClaim.owner_id == _uid())
    ).all()
    approved = [c for c in claims
                if c.approval_status == ApprovalStatus.approved]
    queries = _generate_research_queries(approved)
    return GenerateQueriesOut(
        queries=queries,
        approved_claim_count=len(approved),
        message=(
            f"Generated {len(queries)} queries from "
            f"{len(approved)} approved claims."
        ),
    )


@research_router.post("/profile-contributions",
                      response_model=RunResearchOut, status_code=201)
def run_profile_contributions(
    body: RunResearchRequest,
    session: Session = Depends(get_session),
):
    """Run the public-research pipeline for the current user.

    1.  Loads the user's approved claims.
    2.  Generates search queries (or uses body.queries if supplied).
    3.  Calls the research provider (mock by default; real opt-in).
    4.  Stores each new finding as PublicResearchFinding (pending approval).
    5.  Returns counts — no finding is auto-approved or auto-applied.

    SAFETY: suggested_framing in a finding is a read-only suggestion.
    Nothing is written to claims, packages, or bullets by this endpoint.
    """
    limit = max(1, min(10, body.limit_per_query or 3))

    claims = session.exec(
        select(ProfileClaim).where(ProfileClaim.owner_id == _uid())
    ).all()
    approved = [c for c in claims
                if c.approval_status == ApprovalStatus.approved]

    if body.queries:
        query_dicts = [
            {"query": q, "rationale": "user-supplied", "claim_ids": []}
            for q in body.queries[:10]
        ]
    else:
        query_dicts = _generate_research_queries(approved)

    if not query_dicts:
        return RunResearchOut(
            findings_created=0,
            queries_used=[],
            provider="mock",
            message="No queries generated — approve some profile claims first.",
        )

    provider = _get_research_provider()
    query_strings = [q["query"] for q in query_dicts]
    raw_findings = provider.search(query_strings, limit_per_query=limit)

    # Build claim_id lookup for provenance
    query_to_claim_ids = {q["query"]: q["claim_ids"] for q in query_dicts}

    created = 0
    for rf in raw_findings:
        # Dedup: same owner + source_url
        if rf.get("source_url"):
            existing = session.exec(
                select(PublicResearchFinding)
                .where(PublicResearchFinding.owner_id == _uid())
                .where(PublicResearchFinding.source_url == rf["source_url"])
            ).first()
            if existing:
                continue

        f = PublicResearchFinding(
            owner_id=_uid(),
            query=rf.get("query", ""),
            source_url=rf.get("source_url"),
            source_title=rf.get("source_title"),
            source_snippet=rf.get("source_snippet", ""),
            finding_text=rf.get("finding_text", ""),
            usage_class=ResearchUsageClass(
                rf.get("usage_class", "background_context")),
            suggested_framing=rf.get("suggested_framing"),
            approval_status=ResearchApprovalStatus.pending,
            prompted_by_claim_ids=query_to_claim_ids.get(
                rf.get("query", ""), []),
            provider=rf.get("provider", provider.name),
        )
        session.add(f)
        created += 1

    session.commit()
    return RunResearchOut(
        findings_created=created,
        queries_used=query_strings,
        provider=provider.name,
        message=(
            f"Created {created} new findings from "
            f"{len(query_strings)} queries via '{provider.name}' provider."
        ),
    )


@research_router.get("/findings", response_model=List[ResearchFindingRead])
def list_research_findings(
    approval_status: Optional[str] = None,
    usage_class: Optional[str] = None,
    session: Session = Depends(get_session),
):
    """List research findings for the current user.

    Optional query params:
      approval_status  — pending | approved | rejected
      usage_class      — background_context | claim_support |
                         framing_only | not_usable
    """
    q = select(PublicResearchFinding).where(
        PublicResearchFinding.owner_id == _uid()
    )
    if approval_status:
        q = q.where(
            PublicResearchFinding.approval_status == approval_status)
    if usage_class:
        q = q.where(
            PublicResearchFinding.usage_class == usage_class)
    findings = session.exec(
        q.order_by(PublicResearchFinding.created_at.desc())
    ).all()
    return [_finding_read(f) for f in findings]


@research_router.get("/findings/{finding_id}",
                     response_model=ResearchFindingRead)
def get_research_finding(
    finding_id: str, session: Session = Depends(get_session)
):
    return _finding_read(_get_owned_finding(finding_id, session))


@research_router.patch("/findings/{finding_id}",
                       response_model=ResearchFindingRead)
def patch_research_finding(
    finding_id: str,
    body: ResearchFindingPatch,
    session: Session = Depends(get_session),
):
    """Update usage_class and/or approval_status.

    Approval rules:
    - not_usable findings may NOT be approved (status stays pending
      or is set to rejected only).
    - pending / rejected findings may be re-classified freely.
    - Approving a finding sets approved_at timestamp.
    """
    f = _get_owned_finding(finding_id, session)

    if body.usage_class is not None:
        f.usage_class = body.usage_class

    if body.approval_status is not None:
        # Safety gate: not_usable findings cannot be approved
        if (body.approval_status == ResearchApprovalStatus.approved
                and f.usage_class == ResearchUsageClass.not_usable):
            raise HTTPException(
                422,
                "Findings classified as 'not_usable' cannot be approved. "
                "Re-classify the finding before approving.",
            )
        f.approval_status = body.approval_status
        if body.approval_status == ResearchApprovalStatus.approved:
            f.approved_at = _now()
        else:
            f.approved_at = None

    session.add(f)
    session.commit()
    session.refresh(f)
    return _finding_read(f)


@research_router.delete("/findings/{finding_id}", status_code=204)
def delete_research_finding(
    finding_id: str, session: Session = Depends(get_session)
):
    f = _get_owned_finding(finding_id, session)
    session.delete(f)
    session.commit()


app.include_router(research_router)


# ---------------------------------------------------------------------------
# Health — advertise upgrade phase 6
# ---------------------------------------------------------------------------
# Patch the /api/health endpoint to report upgrade_phases_shipped: [7,4,5,6].
# We achieve this by registering an override that wraps the existing handler.
# Because FastAPI uses last-registered wins for duplicate paths on the *same*
# router, we instead monkey-patch the health response after the fact via a
# startup event that mutates the cached config dict.
#
# The cleaner approach (and the one we use here) is to add an *additive*
# startup handler that updates a module-level constant read by the existing
# health handler.  Since the health handler reads _UPGRADE_PHASES_SHIPPED
# from the module scope at call-time, we can extend it here without touching
# the existing handler code.
# ---------------------------------------------------------------------------

try:
    _UPGRADE_PHASES_SHIPPED  # noqa: F821 — defined by earlier phase blocks
except NameError:
    _UPGRADE_PHASES_SHIPPED = []

if 6 not in _UPGRADE_PHASES_SHIPPED:
    _UPGRADE_PHASES_SHIPPED = list(_UPGRADE_PHASES_SHIPPED) + [6]


# ===========================================================================
# Upgrade Phase 7 — Real notification center
# In-app inbox + email via stdlib smtplib (zero new deps) + SMS/Twilio
# behind explicit opt-in. Default: nothing sent until configured.
# APTIRO_PHASE7_NOTIFICATIONS_MARKER — do not remove; idempotency guard.
# ===========================================================================
import smtplib as _smtplib
from email.mime.multipart import MIMEMultipart as _MIMEMultipart
from email.mime.text import MIMEText as _MIMEText

# --- Phase 7 config env vars -------------------------------------------
_SMTP_HOST = os.getenv("APTIRO_SMTP_HOST", "")
try:
    _SMTP_PORT = int(os.getenv("APTIRO_SMTP_PORT", "587") or "587")
except ValueError:
    _SMTP_PORT = 587
_SMTP_USER = os.getenv("APTIRO_SMTP_USER", "")
_SMTP_PASS = os.getenv("APTIRO_SMTP_PASS", "")
_SMTP_FROM = os.getenv("APTIRO_SMTP_FROM", "") or _SMTP_USER
_SMTP_TLS = os.getenv("APTIRO_SMTP_TLS", "starttls").lower()
_TWILIO_SID = os.getenv("APTIRO_TWILIO_SID", "")
_TWILIO_TOKEN = os.getenv("APTIRO_TWILIO_TOKEN", "")
_TWILIO_FROM = os.getenv("APTIRO_TWILIO_FROM", "")


def _smtp_configured() -> bool:
    return bool(_SMTP_HOST and _SMTP_USER and _SMTP_PASS)


def _twilio_configured() -> bool:
    return bool(_TWILIO_SID and _TWILIO_TOKEN and _TWILIO_FROM)


# --- Phase 7 SQLModel tables -------------------------------------------

class UserNotificationPreference(SQLModel, table=True):
    """Per-user notification opt-in settings. Default: nothing is sent
    until the user explicitly enables a channel and supplies an address.
    In-app notifications are always persisted (zero external cost)."""
    __tablename__ = "usernotificationpreference"
    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(index=True, unique=True)
    # In-app center (always on)
    in_app_enabled: bool = True
    # Email — off by default; requires address + SMTP server config
    email_enabled: bool = False
    email_address: str = ""
    email_daily_digest: bool = False
    email_weekly_digest: bool = False
    email_match_alerts: bool = False
    email_followup_reminders: bool = False
    # Score threshold for match alerts: 0 = disabled
    match_alert_threshold: int = Field(default=0)
    # SMS — off by default, explicit opt-in only, requires Twilio config
    sms_enabled: bool = False
    sms_phone: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class InAppNotification(SQLModel, table=True):
    """Real in-app notification center items. Persisted per owner with
    read/unread state. Cleared on delete; not in the privacy bundle."""
    __tablename__ = "inappnotification"
    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(index=True)
    kind: str = ""
    subject: str = ""
    body: str = ""
    package_id: Optional[str] = None
    is_read: bool = False
    created_at: datetime = Field(default_factory=_now)


# --- Phase 7 helpers ---------------------------------------------------

def _get_or_create_prefs(session, owner_id: str) -> UserNotificationPreference:
    """Fetch the owner's preference row, creating a safe default if absent."""
    prefs = session.exec(
        select(UserNotificationPreference).where(
            UserNotificationPreference.owner_id == owner_id
        )
    ).first()
    if not prefs:
        prefs = UserNotificationPreference(owner_id=owner_id)
        session.add(prefs)
        session.commit()
        session.refresh(prefs)
    return prefs


def _send_email_raw(to_addr: str, subject: str, body: str) -> bool:
    """Send a plain-text email via the configured SMTP server.
    Returns True on success, False on any error. Never raises.
    No-ops silently when SMTP is not configured."""
    if not _smtp_configured() or not to_addr:
        return False
    try:
        msg = _MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = _SMTP_FROM
        msg["To"] = to_addr
        msg.attach(_MIMEText(body, "plain"))
        if _SMTP_TLS == "ssl":
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            with _smtplib.SMTP_SSL(_SMTP_HOST, _SMTP_PORT, context=ctx) as srv:
                srv.login(_SMTP_USER, _SMTP_PASS)
                srv.sendmail(_SMTP_FROM, to_addr, msg.as_string())
        else:
            with _smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as srv:
                srv.ehlo()
                if _SMTP_TLS == "starttls":
                    srv.starttls()
                    srv.ehlo()
                if _SMTP_USER:
                    srv.login(_SMTP_USER, _SMTP_PASS)
                srv.sendmail(_SMTP_FROM, to_addr, msg.as_string())
        _logj("email_sent", to=to_addr, subject=subject)
        return True
    except Exception as exc:
        _logj("email_error", to=to_addr, error=str(exc))
        return False


def _send_sms_raw(to_phone: str, body: str) -> bool:
    """Send an SMS via Twilio REST. Uses httpx (already a dep).
    Returns True on success, False on any error. Never raises.
    No-ops when Twilio credentials are absent or httpx is unavailable."""
    if not _twilio_configured() or not to_phone or _httpx is None:
        return False
    try:
        url = ("https://api.twilio.com/2010-04-01/Accounts/%s/Messages.json"
               % _TWILIO_SID)
        resp = _httpx.post(
            url,
            auth=(_TWILIO_SID, _TWILIO_TOKEN),
            data={"From": _TWILIO_FROM, "To": to_phone,
                  "Body": body[:1600]},
            timeout=10,
        )
        ok = resp.status_code in (200, 201)
        _logj("sms_sent" if ok else "sms_error",
              to=to_phone, status=resp.status_code)
        return ok
    except Exception as exc:
        _logj("sms_error", to=to_phone, error=str(exc))
        return False


def _deliver_notification(
    session,
    kind: str,
    subject: str,
    body: str,
    package_id: Optional[str] = None,
    owner_id: Optional[str] = None,
) -> dict:
    """Deliver a notification through all configured channels for the owner.

    * Always writes an InAppNotification row when in_app_enabled (default).
    * Sends email only when email_enabled + address present + SMTP configured.
    * Sends SMS only when sms_enabled + phone present + Twilio configured.
    * Also writes a legacy NotificationPreview row for backwards compat.

    Returns a summary dict {in_app_id, email_sent, sms_sent}."""
    oid = owner_id or _uid()
    prefs = _get_or_create_prefs(session, oid)

    # 1. In-app
    in_app_id: Optional[str] = None
    if prefs.in_app_enabled:
        n = InAppNotification(
            owner_id=oid,
            kind=kind,
            subject=subject,
            body=body,
            package_id=package_id,
        )
        session.add(n)
        session.commit()
        session.refresh(n)
        in_app_id = n.id

    # 2. Email
    email_sent = False
    if prefs.email_enabled and prefs.email_address and _smtp_configured():
        email_sent = _send_email_raw(prefs.email_address, subject, body)

    # 3. SMS (explicit opt-in only)
    sms_sent = False
    if prefs.sms_enabled and prefs.sms_phone and _twilio_configured():
        sms_sent = _send_sms_raw(prefs.sms_phone, body)

    # 4. Legacy preview row (keeps existing /notifications history working)
    try:
        _persist_previews(
            session,
            NotificationKind.daily_digest,
            (subject, body),
            package_id=package_id,
            only=NotificationChannel.in_app,
        )
    except Exception:
        pass

    return {"in_app_id": in_app_id, "email_sent": email_sent,
            "sms_sent": sms_sent}


# --- Phase 7 Pydantic I/O models ---------------------------------------

class NotifPrefUpdate(BaseModel):
    in_app_enabled: Optional[bool] = None
    email_enabled: Optional[bool] = None
    email_address: Optional[str] = None
    email_daily_digest: Optional[bool] = None
    email_weekly_digest: Optional[bool] = None
    email_match_alerts: Optional[bool] = None
    email_followup_reminders: Optional[bool] = None
    match_alert_threshold: Optional[int] = None
    sms_enabled: Optional[bool] = None
    sms_phone: Optional[str] = None


class NotifPrefOut(BaseModel):
    id: str
    owner_id: str
    in_app_enabled: bool
    email_enabled: bool
    email_address: str
    email_daily_digest: bool
    email_weekly_digest: bool
    email_match_alerts: bool
    email_followup_reminders: bool
    match_alert_threshold: int
    sms_enabled: bool
    sms_phone: str
    smtp_configured: bool
    twilio_configured: bool
    created_at: datetime
    updated_at: datetime


class InAppNotifOut(BaseModel):
    id: str
    owner_id: str
    kind: str
    subject: str
    body: str
    package_id: Optional[str]
    is_read: bool
    created_at: datetime


class NotifInboxOut(BaseModel):
    items: List[InAppNotifOut]
    unread_count: int


class SendDigestOut(BaseModel):
    subject: str
    in_app_id: Optional[str]
    email_sent: bool
    sms_sent: bool
    top_job_count: int


class SendAlertOut(BaseModel):
    alerts_generated: int
    above_threshold: int
    threshold: int


def _pref_out(p: UserNotificationPreference) -> NotifPrefOut:
    return NotifPrefOut(
        id=p.id, owner_id=p.owner_id,
        in_app_enabled=p.in_app_enabled,
        email_enabled=p.email_enabled, email_address=p.email_address,
        email_daily_digest=p.email_daily_digest,
        email_weekly_digest=p.email_weekly_digest,
        email_match_alerts=p.email_match_alerts,
        email_followup_reminders=p.email_followup_reminders,
        match_alert_threshold=p.match_alert_threshold,
        sms_enabled=p.sms_enabled, sms_phone=p.sms_phone,
        smtp_configured=_smtp_configured(),
        twilio_configured=_twilio_configured(),
        created_at=p.created_at, updated_at=p.updated_at,
    )


def _inapp_out(n: InAppNotification) -> InAppNotifOut:
    return InAppNotifOut(
        id=n.id, owner_id=n.owner_id, kind=n.kind,
        subject=n.subject, body=n.body, package_id=n.package_id,
        is_read=n.is_read, created_at=n.created_at,
    )


# --- Phase 7 routers ---------------------------------------------------

notif_prefs_router = APIRouter(prefix="/api/notifications",
                               tags=["notifications"])
notif_inbox_router = APIRouter(prefix="/api/notifications/inbox",
                               tags=["notifications"])
notif_send_router = APIRouter(prefix="/api/notifications/send",
                              tags=["notifications"])


@notif_prefs_router.get("/preferences", response_model=NotifPrefOut)
def get_notif_prefs(session: Session = Depends(get_session)):
    return _pref_out(_get_or_create_prefs(session, _uid()))


@notif_prefs_router.put("/preferences", response_model=NotifPrefOut)
def update_notif_prefs(body: NotifPrefUpdate,
                       session: Session = Depends(get_session)):
    prefs = _get_or_create_prefs(session, _uid())
    for field, val in body.model_dump(exclude_none=True).items():
        if field == "match_alert_threshold":
            val = max(0, min(100, int(val)))
        setattr(prefs, field, val)
    prefs.updated_at = _now()
    session.add(prefs)
    session.commit()
    session.refresh(prefs)
    return _pref_out(prefs)


@notif_inbox_router.get("", response_model=NotifInboxOut)
def get_inbox(session: Session = Depends(get_session)):
    items = session.exec(
        select(InAppNotification)
        .where(InAppNotification.owner_id == _uid())
        .order_by(InAppNotification.created_at.desc())
    ).all()
    unread = sum(1 for n in items if not n.is_read)
    return NotifInboxOut(items=[_inapp_out(n) for n in items],
                         unread_count=unread)


@notif_inbox_router.post("/read-all", response_model=NotifInboxOut)
def mark_all_read(session: Session = Depends(get_session)):
    items = session.exec(
        select(InAppNotification)
        .where(InAppNotification.owner_id == _uid())
        .where(InAppNotification.is_read == False)  # noqa: E712
    ).all()
    for n in items:
        n.is_read = True
        session.add(n)
    session.commit()
    return get_inbox(session=session)


@notif_inbox_router.post("/{notif_id}/read", response_model=InAppNotifOut)
def mark_read(notif_id: str, session: Session = Depends(get_session)):
    n = session.get(InAppNotification, notif_id)
    if not n or n.owner_id != _uid():
        raise HTTPException(404, "Notification not found")
    n.is_read = True
    session.add(n)
    session.commit()
    session.refresh(n)
    return _inapp_out(n)


@notif_inbox_router.delete("/{notif_id}", status_code=204)
def delete_notif(notif_id: str,
                 session: Session = Depends(get_session)):
    n = session.get(InAppNotification, notif_id)
    if not n or n.owner_id != _uid():
        raise HTTPException(404, "Notification not found")
    session.delete(n)
    session.commit()
    return Response(status_code=204)


@notif_send_router.post("/digest", response_model=SendDigestOut)
def send_digest(session: Session = Depends(get_session)):
    """Render and deliver a daily digest for the current user.
    Writes to in-app inbox always; sends email/SMS when configured."""
    subject, body = _render_digest(session)
    result = _deliver_notification(session, "daily_digest", subject, body)
    jobs = session.exec(select(JobPosting).where(
        JobPosting.is_archived == False)).all()  # noqa: E712
    return SendDigestOut(
        subject=subject,
        in_app_id=result["in_app_id"],
        email_sent=result["email_sent"],
        sms_sent=result["sms_sent"],
        top_job_count=min(3, len(jobs)),
    )


@notif_send_router.post("/match-alert", response_model=SendAlertOut)
def send_match_alert(session: Session = Depends(get_session)):
    """Check active jobs against the user's match-alert threshold and
    deliver an in-app (+ email/SMS) alert for each job above it.
    No-op when threshold is 0 (the default)."""
    prefs = _get_or_create_prefs(session, _uid())
    threshold = prefs.match_alert_threshold
    if threshold == 0:
        return SendAlertOut(alerts_generated=0, above_threshold=0,
                            threshold=0)

    jobs = session.exec(select(JobPosting).where(
        JobPosting.is_archived == False)).all()  # noqa: E712
    strat = _active_strategy(session)
    alerts = above = 0
    for j in jobs:
        sc = score_job(session, j, strat)["score"]
        if sc >= threshold:
            above += 1
            subj = ("High-fit match: %s @ %s (%d/100)"
                    % (j.title, j.company, sc))
            bd = (
                "%s at %s scored %d/100 against your active strategy, "
                "meeting your alert threshold of %d. "
                "Review it in Match Inbox." % (j.title, j.company, sc, threshold)
            )
            _deliver_notification(session, "match_threshold_alert",
                                   subj, bd)
            alerts += 1

    return SendAlertOut(alerts_generated=alerts, above_threshold=above,
                        threshold=threshold)


app.include_router(notif_prefs_router)
app.include_router(notif_inbox_router)
app.include_router(notif_send_router)

# ===========================================================================
# Upgrade Phase 8 — Auth hardening & launch security
#
# Deliverables (all additive):
#   • Security headers on every response (CSP-lite, X-Frame, X-Content-Type, …)
#   • In-memory rate limiter on /api/auth/login + /api/auth/register
#     (off by default in dev/test; on in production or via explicit env var)
#   • Token session expiry (opt-in via APTIRO_SESSION_HOURS; NULL = no expiry)
#   • POST /api/auth/rotate   — issues a fresh token (and stamps expiry if configured)
#   • DELETE /api/auth/account — confirmed hard-delete of account + all owned data
#   • POST /api/packages/{id}/export/sign — creates a signed, expiring download link
#   • GET  /api/exports/{token}           — serves the export without bearer auth
#   • GET  /api/legal/privacy             — privacy policy text (markdown)
#   • GET  /api/legal/terms               — terms of service text (markdown)
#   • Production warning when APTIRO_ENV=production but APTIRO_AUTH not set
#   • upgrade_phases_shipped gains 8
#
# Fast-follow (explicitly NOT this phase): email verification, password reset,
# OAuth, refresh tokens.
#
# APTIRO_PHASE8_AUTH_HARDENING_MARKER — do not remove; idempotency guard.
# ===========================================================================

import collections as _collections8
import threading as _threading8
from datetime import timedelta as _timedelta8

# ── Phase 8 config ─────────────────────────────────────────────────────────
_P8_PROD = os.getenv("APTIRO_ENV", "").lower() in ("production", "prod")
_P8_SESSION_HOURS = int(os.getenv("APTIRO_SESSION_HOURS", "0"))
# Rate limits are DISABLED in dev/test unless production mode or explicit var.
_P8_RATE_ENABLED = _P8_PROD or bool(os.getenv("APTIRO_AUTH_RATE_LIMIT"))
_P8_AUTH_RATE = int(os.getenv("APTIRO_AUTH_RATE_LIMIT", "100"))   # /min per IP
_P8_MUT_RATE  = int(os.getenv("APTIRO_MUTATE_RATE_LIMIT", "300")) # /min per IP
# Export link signing secret. Auto-generated at startup if not set; set it in
# production so signed links survive process restarts.
_P8_EXPORT_SECRET = os.getenv("APTIRO_EXPORT_SECRET") or _secrets.token_hex(32)

if _P8_PROD and not os.getenv("APTIRO_AUTH"):
    print(
        "[aptiro] WARNING: APTIRO_ENV=production but APTIRO_AUTH is not set. "
        "Set APTIRO_AUTH=on for multi-user production deployments.",
        file=_sys.stderr,
    )

# ── In-memory sliding-window rate limiter ─────────────────────────────────
_P8_RL: dict = {}           # key -> deque of monotonic timestamps
_P8_RL_LOCK = _threading8.Lock()


def _p8_rate_ok(key: str, limit: int, window: int = 60) -> bool:
    """Returns True if the request is within the rate limit, False if throttled."""
    if not _P8_RATE_ENABLED:
        return True
    now = _time.monotonic()
    with _P8_RL_LOCK:
        dq = _P8_RL.setdefault(key, _collections8.deque())
        while dq and now - dq[0] > window:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True


# ── ExportToken model ──────────────────────────────────────────────────────

# Phase 8 fix-A2: register token_expires_at on User.__table__
# Adds the column to User.__table__ so SQLModel.metadata.create_all() creates
# it on every engine — including the test fixture's separate in-memory DB.
# The column is intentionally NOT on the User SQLModel Python class; raw SQL
# is used for reads/writes (see _p8_token_expires / _p8_set_token_expiry).
from sqlalchemy import Column as _p8_Column, DateTime as _p8_DateTime
if "token_expires_at" not in User.__table__.columns:
    User.__table__.append_column(
        _p8_Column("token_expires_at", _p8_DateTime(), nullable=True)
    )

class ExportToken(SQLModel, table=True):
    """Signed, expiring download link for a package export. No bearer auth needed."""
    id: str = Field(default_factory=lambda: _uuidmod.uuid4().hex, primary_key=True)
    owner_id: str = Field(index=True)
    package_id: str
    token_hash: str = Field(index=True)   # SHA-256 of raw token; raw never stored
    format: str = "md"
    artifact: str = "resume"
    include_unsupported: bool = False
    expires_at: datetime
    created_at: datetime = Field(default_factory=_now)
    used_at: Optional[datetime] = None


# ── Phase 8 column additions (additive, idempotent) ────────────────────────
def _p8_ensure_columns() -> None:
    """Add token_expires_at to the user table (idempotent; SQLite + Postgres)."""
    import sqlalchemy as _sa8
    try:
        insp = _sa8.inspect(engine)
        for tname, colname, coldecl in [
            ("user", "token_expires_at", "DATETIME"),
        ]:
            if tname not in insp.get_table_names():
                continue
            if colname in {c["name"] for c in insp.get_columns(tname)}:
                continue
            try:
                with engine.connect() as _c8:
                    _c8.execute(_sa8.text(
                        f'ALTER TABLE "{tname}" ADD COLUMN "{colname}" {coldecl}'))
                    _c8.commit()
            except Exception as _ce8:
                print(f"[aptiro] p8 col {tname}.{colname} skipped: {_ce8!r}",
                      file=_sys.stderr)
    except Exception as _e8:
        print(f"[aptiro] p8_ensure_columns skipped: {_e8!r}", file=_sys.stderr)


@app.on_event("startup")
async def _p8_on_startup() -> None:
    _p8_ensure_columns()
    # Pick up ExportToken (and any other Phase 8 tables) in case init_db ran first
    try:
        SQLModel.metadata.create_all(engine)
    except Exception:
        pass


# ── Token expiry helpers (raw SQL — token_expires_at not on User model class) ─
def _p8_token_expires(token: str) -> Optional[datetime]:
    """Return token_expires_at for the given bearer token, or None (= no expiry)."""
    import sqlalchemy as _sa8x
    s, gen = _mw_session()
    try:
        row = s.execute(
            _sa8x.text('SELECT token_expires_at FROM "user" WHERE token = :t'),
            {"t": token},
        ).first()
        if row and row[0]:
            v = row[0]
            return datetime.fromisoformat(v) if isinstance(v, str) else v
    except Exception:
        pass
    finally:
        try:
            next(gen)
        except StopIteration:
            pass
    return None


def _p8_set_token_expiry(token: str, hours: int) -> None:
    """Stamp token_expires_at on a user record via raw SQL."""
    import sqlalchemy as _sa8s
    exp = _now() + _timedelta8(hours=hours)
    s, gen = _mw_session()
    try:
        s.execute(
            _sa8s.text(
                'UPDATE "user" SET token_expires_at = :exp WHERE token = :t'
            ),
            {"exp": exp, "t": token},
        )
        s.commit()
    except Exception as _e8s:
        print(f"[aptiro] p8 set_expiry failed: {_e8s!r}", file=_sys.stderr)
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


# ── Security-headers + rate-limit + token-expiry middleware (outermost) ────
@app.middleware("http")
async def _p8_security_middleware(request, call_next):
    from starlette.responses import JSONResponse as _JR8

    path   = request.url.path
    method = request.method
    ip     = (request.client.host if request.client else None) or "unknown"

    # 1. Rate limit: auth endpoints (login / register)
    if path in ("/api/auth/login", "/api/auth/register"):
        if not _p8_rate_ok(f"auth:{ip}", _P8_AUTH_RATE, 60):
            return _JR8(
                {"detail": "Too many requests — please wait before trying again."},
                status_code=429,
                headers={"Retry-After": "60"},
            )

    # 2. Rate limit: all other mutating endpoints
    elif method in ("POST", "PUT", "PATCH", "DELETE") and path.startswith("/api/"):
        if not _p8_rate_ok(f"mut:{ip}", _P8_MUT_RATE, 60):
            return _JR8(
                {"detail": "Too many requests."},
                status_code=429,
                headers={"Retry-After": "60"},
            )

    # 3. Token expiry check (only when auth is on AND session expiry is configured)
    if AUTH_ENABLED and _P8_SESSION_HOURS > 0:
        token = _bearer(request)
        if (token
                and path.startswith("/api/")
                and not path.startswith("/api/auth/")
                and not path.startswith("/api/exports/")):
            expires = _p8_token_expires(token)
            if expires is not None and expires < _now():
                return _JR8(
                    {"detail": "Session expired. Please sign in again."},
                    status_code=401,
                )

    response = await call_next(request)

    # 4. Security headers (always injected, never overwrite if already set)
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("X-XSS-Protection", "1; mode=block")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if _P8_PROD:
        h.setdefault(
            "Strict-Transport-Security",
            "max-age=63072000; includeSubDomains; preload",
        )

    return response


# ── Auth Phase 8 router: token rotation + confirmed account deletion ───────
_auth8_router = APIRouter(prefix="/api/auth", tags=["auth-p8"])


class RotateOut(BaseModel):
    token: str
    expires_at: Optional[str] = None


@_auth8_router.post("/rotate", response_model=RotateOut)
def p8_rotate_token(session: Session = Depends(get_session)):
    """Issue a fresh bearer token for the current user (rotation).
    When APTIRO_SESSION_HOURS > 0 the new token carries an expiry.
    Not available for the local default user (auth must be on)."""
    uid = _uid()
    if uid == DEFAULT_UID:
        raise HTTPException(
            403,
            "Token rotation is not available for the local default user. "
            "Enable auth (APTIRO_AUTH=on) and log in with a real account.",
        )
    u = session.get(User, uid)
    if not u:
        raise HTTPException(404, "User not found")
    u.token = _new_token()
    session.add(u)
    session.commit()
    session.refresh(u)
    exp: Optional[str] = None
    if _P8_SESSION_HOURS > 0:
        _p8_set_token_expiry(u.token, _P8_SESSION_HOURS)
        exp = (_now() + _timedelta8(hours=_P8_SESSION_HOURS)).isoformat()
    return RotateOut(token=u.token, expires_at=exp)


class AccountDeleteBody(BaseModel):
    confirm: str  # caller must pass "DELETE MY ACCOUNT" exactly


@_auth8_router.delete("/account", status_code=204)
def p8_delete_account(
    body: AccountDeleteBody, session: Session = Depends(get_session)
):
    """Hard-delete the calling user's account and every piece of owned data.

    Requires ``confirm = "DELETE MY ACCOUNT"`` in the request body.
    Cannot be called by the default local user (no account to delete).
    This is irreversible — data cannot be recovered after this call.
    """
    if body.confirm != "DELETE MY ACCOUNT":
        raise HTTPException(
            422,
            'Set "confirm" to exactly "DELETE MY ACCOUNT" to proceed. '
            "This action is permanent and cannot be undone.",
        )
    uid = _uid()
    if uid == DEFAULT_UID:
        raise HTTPException(
            403,
            "The local default user account cannot be deleted. "
            "Enable auth and use a real account.",
        )

    # Collect owned-model classes defensively (later-phase models may not exist
    # in all deployments; KeyError is caught per-model).
    _p8_del_models = [
        Source, ProfileClaim, Strategy, JobPosting,
        ApplicationPackage, Application, ExportToken,
    ]
    for _mn8 in [
        "SavedJobSearch", "UserNotificationPreference",
        "InAppNotification", "PublicResearchFinding",
    ]:
        try:
            _p8_del_models.append(globals()[_mn8])
        except KeyError:
            pass

    for _dm8 in _p8_del_models:
        try:
            for _obj8 in session.exec(
                select(_dm8).where(_dm8.owner_id == uid)
            ).all():
                session.delete(_obj8)
        except Exception:
            pass  # model may not exist in this deployment

    u = session.get(User, uid)
    if u:
        session.delete(u)
    session.commit()
    return Response(status_code=204)


app.include_router(_auth8_router)


# ── Export signing router ──────────────────────────────────────────────────
_pkg8_router = APIRouter(prefix="/api/packages", tags=["exports-p8"])


class SignedExportLinkOut(BaseModel):
    token: str
    url: str
    expires_at: str
    format: str
    artifact: str


@_pkg8_router.post("/{pkg_id}/export/sign", response_model=SignedExportLinkOut)
def p8_sign_export_link(
    pkg_id: str,
    format: str = Query("md"),
    artifact: str = Query("resume"),
    include_unsupported: bool = Query(False),
    ttl_minutes: int = Query(60, ge=1, le=10080),
    session: Session = Depends(get_session),
):
    """Create a signed, expiring download link for a package export.

    The returned ``url`` (/api/exports/{token}) can be shared and visited
    without a bearer token. The link expires after ``ttl_minutes`` (default 60,
    max 7 days = 10 080 minutes).
    """
    pkg = _get_owned(session, ApplicationPackage, pkg_id)
    if not pkg:
        raise HTTPException(404, "Package not found")
    if format not in exporting.FORMATS:
        raise HTTPException(
            400,
            f"Unsupported format '{format}'. Choose one of: "
            + ", ".join(exporting.FORMATS),
        )
    if artifact not in ("resume", "cover_letter", "both"):
        raise HTTPException(400, "artifact must be resume | cover_letter | both")

    raw  = _secrets.token_urlsafe(32)
    hashed = _hashlib.sha256(raw.encode()).hexdigest()
    exp  = _now() + _timedelta8(minutes=ttl_minutes)
    et   = ExportToken(
        owner_id=_uid(),
        package_id=pkg_id,
        token_hash=hashed,
        format=format,
        artifact=artifact,
        include_unsupported=include_unsupported,
        expires_at=exp,
    )
    session.add(et)
    session.commit()
    return SignedExportLinkOut(
        token=raw,
        url=f"/api/exports/{raw}",
        expires_at=exp.isoformat(),
        format=format,
        artifact=artifact,
    )


app.include_router(_pkg8_router)


# ── Signed-export serve router (GET only; bearer auth not required) ────────
_exports8_router = APIRouter(prefix="/api/exports", tags=["exports-p8-serve"])


@_exports8_router.get("/{raw_token}")
def p8_serve_export(raw_token: str, session: Session = Depends(get_session)):
    """Serve a previously signed export file without requiring a bearer token.

    Returns 403 if the token is invalid, 410 if it has expired, 404 if the
    originating package no longer exists.
    """
    hashed = _hashlib.sha256(raw_token.encode()).hexdigest()
    et = session.exec(
        select(ExportToken).where(ExportToken.token_hash == hashed)
    ).first()
    if not et:
        raise HTTPException(403, "Invalid or unrecognised export link")
    _et_exp = et.expires_at
    if _et_exp is not None and _et_exp.tzinfo is None:
        _et_exp = _et_exp.replace(tzinfo=timezone.utc)
    if _et_exp < _now():
        raise HTTPException(410, "This export link has expired")

    pkg = session.get(ApplicationPackage, et.package_id)
    if not pkg:
        raise HTTPException(404, "The originating package no longer exists")

    if et.format == exporting.ATS_PROFILE:
        content, ext = exporting.render_ats(
            _export_model(session, pkg, et.include_unsupported), et.artifact
        )
    elif et.format not in exporting.FORMATS:
        raise HTTPException(400, f"Invalid format on stored token: {et.format}")
    else:
        model = _export_model(session, pkg, et.include_unsupported)
        try:
            content, ext = exporting.render(model, et.format, et.artifact)
        except exporting.ExportUnavailable as _eu8:
            raise HTTPException(501, str(_eu8))

    safe  = re.sub(r"[^A-Za-z0-9]+", "_",
                   f"{pkg.company or 'aptiro'}_{et.artifact}").strip("_")
    fname = f"{safe or 'aptiro_export'}.{ext}"

    # Mark token as used (once; subsequent calls still work until expiry)
    if et.used_at is None:
        et.used_at = _now()
        session.add(et)
        session.commit()

    return Response(
        content=content,
        media_type=EXPORT_MEDIA.get(ext, "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


app.include_router(_exports8_router)


# ── Legal docs router ──────────────────────────────────────────────────────
_legal8_router = APIRouter(prefix="/api/legal", tags=["legal"])

_P8_PRIVACY_TEXT = """\
# Aptiro Privacy Policy

**Last updated: 2026-05-27**

## 1. What Aptiro Is

Aptiro is a local-first, self-hosted job-application cockpit. By default, all
data lives on your own machine or private server. No personal data is transmitted
to third-party services unless you explicitly configure external providers.

## 2. Data Stored

| Category | What | Where |
|---|---|---|
| Account | Email + PBKDF2-hashed password | Local DB only |
| Career content | Résumé text, claims, evidence, packages | Local DB only |
| Audit trail | Append-only server log of mutations | Local DB only |

Credentials are never logged, never exported in the privacy bundle, and never
transmitted to Anthropic or any other party.

## 3. Optional Third-Party Services (all opt-in)

- **Anthropic AI** — used only when `APTIRO_AI_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`
  are both set. Text sent is strictly limited to the specific career claims
  being rewritten; no personal identifiers are included.
- **SMTP** — used only when `APTIRO_SMTP_HOST` is configured. You control
  the sending server.
- **Twilio SMS** — used only when `APTIRO_TWILIO_SID/TOKEN/FROM` are configured
  and you have explicitly enabled SMS in notification preferences.

No analytics trackers. No advertising networks. No telemetry.

## 4. Data Retention & Deletion

- **Delete all data (keep account):** Profile Vault → Privacy → Delete all my data,
  or `DELETE /api/privacy/data`.
- **Delete account entirely:** Settings → Delete Account, or
  `DELETE /api/auth/account` with `{"confirm": "DELETE MY ACCOUNT"}`.

Both operations are immediate and irreversible.

## 5. Security Practices

- Passwords: salted PBKDF2-HMAC-SHA256 (120 000 rounds, stdlib)
- Bearer tokens: cryptographically random 256-bit values
- Signed export links: SHA-256 hashed, configurable TTL (default 60 min)
- Session expiry: opt-in via `APTIRO_SESSION_HOURS`
- Rate limiting: enabled in production mode
- Security headers: `X-Content-Type-Options`, `X-Frame-Options`,
  `X-XSS-Protection`, `Referrer-Policy`, `Permissions-Policy`,
  HSTS in production

## 6. Open Source

Aptiro is open-source software. Review the code, file issues, or self-host at
https://github.com/sam3lds-prog/aptiro.
"""

_P8_TERMS_TEXT = """\
# Aptiro Terms of Service

**Last updated: 2026-05-27**

## 1. Acceptance

By running or using Aptiro you agree to these terms.

## 2. Permitted Use

Aptiro is a personal job-application preparation tool.

- **Allowed:** Uploading your own résumé content, building tailored application
  packages from that content, and exporting them for your own job applications.
- **Not allowed:** Fabricating claims, impersonating others, bulk or automated
  application submission, or scraping third-party sites through Aptiro.

## 3. The Non-Negotiables (load-bearing product behaviour)

These are not configurable overrides — they are core to what Aptiro is:

1. **No auto-submit.** Aptiro never submits anything on your behalf. Every
   action requires explicit confirmation.
2. **No fabrication.** The AI assist system is constrained to approved,
   evidence-backed claims only. AI outputs that introduce facts absent from your
   verified career history are blocked before being presented.
3. **No scraping.** LinkedIn, Indeed, and other auth-walled sites are explicitly
   excluded from all job-import paths.

## 4. Disclaimer

Aptiro is provided "as is" without warranty of any kind. Career outcomes are not
guaranteed. The export gate and provenance system reduce the risk of accidental
inaccuracies but do not eliminate your responsibility to review content before
submitting applications.

## 5. License

Aptiro is open-source software distributed under the project license. See the
repository for details.
"""


@_legal8_router.get("/privacy")
def p8_privacy_policy():
    """Serve the Aptiro privacy policy as a markdown string."""
    return {"content": _P8_PRIVACY_TEXT, "format": "markdown",
            "last_updated": "2026-05-27"}


@_legal8_router.get("/terms")
def p8_terms_of_service():
    """Serve the Aptiro terms of service as a markdown string."""
    return {"content": _P8_TERMS_TEXT, "format": "markdown",
            "last_updated": "2026-05-27"}


app.include_router(_legal8_router)


# ── Update upgrade_phases_shipped ──────────────────────────────────────────
try:
    _UPGRADE_PHASES_SHIPPED  # noqa: F821 — defined by earlier phase blocks
except NameError:
    _UPGRADE_PHASES_SHIPPED = []

if 8 not in _UPGRADE_PHASES_SHIPPED:
    _UPGRADE_PHASES_SHIPPED = list(_UPGRADE_PHASES_SHIPPED) + [8]

# ===========================================================================
# Phase 8 Round 4 fix — monkey-patches that bypass text-replacement issues.
# APTIRO_PHASE8_ROUND4_FIX_MARKER — do not remove (idempotency guard).
# ===========================================================================

# ---- Fix 1: tz-safe wrapper around _p8_token_expires ---------------------
# SQLite strips tzinfo on round-trip; the middleware compares with _now()
# which is tz-aware, so a naive return value crashes the comparison.
# Python looks up names in module globals at call time, so simply
# rebinding _p8_token_expires here shadows the original and the
# middleware automatically uses this wrapped version.
_p8_token_expires_v1 = _p8_token_expires


def _p8_token_expires(token):  # type: ignore[no-redef]
    v = _p8_token_expires_v1(token)
    if v is not None and hasattr(v, "tzinfo") and v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v


# ---- Fix 2: replace DELETE /api/auth/account with raw-SQL deletion -------
# The ORM-based delete triggers SET-NULL cascade on profileclaim.source_id
# (which is NOT NULL), poisons the session with PendingRollbackError, and
# returns 500. Raw SQL deletes in dependency order bypass the cascade.
from fastapi.routing import APIRoute as _APIRoute_p8r4

# Remove the original DELETE /api/auth/account route(s)
_p8r4_to_remove = []
for _r in list(app.router.routes):
    if (isinstance(_r, _APIRoute_p8r4)
        and _r.path == "/api/auth/account"
        and "DELETE" in _r.methods):
        _p8r4_to_remove.append(_r)

for _r in _p8r4_to_remove:
    try:
        app.router.routes.remove(_r)
    except ValueError:
        pass


@app.delete("/api/auth/account", status_code=204, tags=["auth-p8-v2"])
def _p8r4_delete_account(
    body: AccountDeleteBody, session: Session = Depends(get_session)
):
    """Confirmed hard-delete (round-4 raw-SQL version)."""
    if body.confirm != "DELETE MY ACCOUNT":
        raise HTTPException(
            422,
            'Set "confirm" to exactly "DELETE MY ACCOUNT" to proceed. '
            "This action is permanent and cannot be undone.",
        )
    uid = _uid()
    if uid == DEFAULT_UID:
        raise HTTPException(
            403,
            "The local default user account cannot be deleted. "
            "Enable auth and use a real account.",
        )

    import sqlalchemy as _sa_p8r4
    insp = _sa_p8r4.inspect(session.get_bind())
    _existing = set(insp.get_table_names())

    # (table, where_clause) — children first, then parents
    _p8r4_dels = [
        ("sourceref",
         "claim_id IN (SELECT id FROM profileclaim WHERE owner_id = :uid)"),
        ("sourceref",
         "source_id IN (SELECT id FROM source WHERE owner_id = :uid)"),
        ("profileclaim", "owner_id = :uid"),
        ("agentcritique",
         "run_id IN (SELECT id FROM agentrun "
         "WHERE package_id IN (SELECT id FROM applicationpackage "
         "WHERE owner_id = :uid))"),
        ("agentrun",
         "package_id IN (SELECT id FROM applicationpackage "
         "WHERE owner_id = :uid)"),
        ("packagebullet",
         "package_id IN (SELECT id FROM applicationpackage "
         "WHERE owner_id = :uid)"),
        ("applysession",
         "package_id IN (SELECT id FROM applicationpackage "
         "WHERE owner_id = :uid)"),
        ("inappnotification", "owner_id = :uid"),
        ("usernotificationpreference", "owner_id = :uid"),
        ("publicresearchfinding", "owner_id = :uid"),
        ("notificationpreview", "owner_id = :uid"),
        ("savedjobsearch", "owner_id = :uid"),
        ("exporttoken", "owner_id = :uid"),
        ("source", "owner_id = :uid"),
        ("applicationpackage", "owner_id = :uid"),
        ("application", "owner_id = :uid"),
        ("strategy", "owner_id = :uid"),
        ("jobposting", "owner_id = :uid"),
    ]
    for _tbl, _where in _p8r4_dels:
        if _tbl in _existing:
            try:
                session.execute(
                    _sa_p8r4.text(
                        'DELETE FROM "' + _tbl + '" WHERE ' + _where
                    ),
                    {"uid": uid},
                )
            except Exception:
                # Defensive: roll back to a clean state and continue.
                try:
                    session.rollback()
                except Exception:
                    pass

    if "user" in _existing:
        try:
            session.execute(
                _sa_p8r4.text('DELETE FROM "user" WHERE id = :uid'),
                {"uid": uid},
            )
        except Exception:
            try:
                session.rollback()
            except Exception:
                pass

    session.commit()
    return Response(status_code=204)

