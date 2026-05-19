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

_DEFAULT_DATABASE_URL = "sqlite:///./aptiro.db"

# Every Aptiro setting is read from an APTIRO_-prefixed environment
# variable so it never collides with generic vars other apps may use.


def _resolve_database_url():
    url = os.getenv("APTIRO_DATABASE_URL") or _DEFAULT_DATABASE_URL
    if url.startswith("sqlite"):
        return url
    if url.startswith(("postgresql", "postgres")):
        if not (_ilu.find_spec("psycopg2") or _ilu.find_spec("psycopg")):
            print(
                f"[aptiro] APTIRO_DATABASE_URL={url!r} needs a Postgres "
                f"driver that is not installed; falling back to "
                f"{_DEFAULT_DATABASE_URL}. `pip install psycopg2-binary` "
                f"for Postgres.",
                file=_sys.stderr,
            )
            return _DEFAULT_DATABASE_URL
    return url


DATABASE_URL = _resolve_database_url()
AI_PROVIDER = os.getenv("APTIRO_AI_PROVIDER", "mock")
EMBEDDING_PROVIDER = os.getenv("APTIRO_EMBEDDING_PROVIDER", "mock")
JOB_PROVIDER = os.getenv("APTIRO_JOB_PROVIDER", "mock")
SEARCH_PROVIDER = os.getenv("APTIRO_SEARCH_PROVIDER", "mock")
NOTIFICATION_PROVIDER = os.getenv("APTIRO_NOTIFICATION_PROVIDER", "mock")
SEED_ON_STARTUP = os.getenv("APTIRO_SEED_ON_STARTUP", "1") == "1"
# URL import guardrails (server-side fetch of a user-supplied public URL
# only - never a crawler). All overridable via APTIRO_-prefixed env.
URL_FETCH_TIMEOUT = float(os.getenv("APTIRO_URL_FETCH_TIMEOUT", "10"))
URL_FETCH_MAX_BYTES = int(
    os.getenv("APTIRO_URL_FETCH_MAX_BYTES", str(2 * 1024 * 1024)))
# Hosts we will not fetch: login/auth-walled or scrape-prohibited.
_URL_FETCH_DENY = {
    "linkedin.com", "www.linkedin.com", "indeed.com", "www.indeed.com",
    "glassdoor.com", "www.glassdoor.com", "facebook.com",
    "www.facebook.com", "x.com", "twitter.com",
}
_IS_SQLITE = DATABASE_URL.startswith("sqlite")


def _uuid():
    return uuid.uuid4().hex


def _now():
    return datetime.now(timezone.utc)


# ===========================================================================
# Enums
# ===========================================================================
class SourceType(str, Enum):
    resume = "resume"
    linkedin = "linkedin"
    profile_text = "profile_text"
    public_url = "public_url"


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


class WorkMode(str, Enum):
    remote = "remote"
    hybrid = "hybrid"
    onsite = "onsite"
    any = "any"


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


# ===========================================================================
# Models
# ===========================================================================
class Source(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    source_type: SourceType
    label: str
    filename: Optional[str] = None
    raw_text: str = ""
    extracted_text: str = ""
    parse_meta: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)
    claims: List["ProfileClaim"] = Relationship(back_populates="source")


class ProfileClaim(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
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


class SourceRef(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    claim_id: str = Field(foreign_key="profileclaim.id", index=True)
    source_id: str = Field(foreign_key="source.id", index=True)
    source_type: SourceType
    section: str = ""
    snippet: str = ""
    page: Optional[int] = None
    confidence: float = 0.0
    claim: Optional[ProfileClaim] = Relationship(back_populates="source_refs")


DEFAULT_WEIGHTS = {
    "role_alignment": 15, "seniority_alignment": 10, "core_skills": 20,
    "domain": 10, "leadership_scope": 10, "ai_technical": 10,
    "evidence_strength": 10, "preferences": 10, "strategy_boost": 5,
}


class Strategy(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str = "Default Strategy"
    is_active: bool = True
    target_roles: List[str] = Field(default_factory=list,
                                    sa_column=Column(JSON))
    region: Optional[str] = None
    work_mode: WorkMode = WorkMode.any
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    aggressiveness: Aggressiveness = Aggressiveness.balanced
    weights: dict = Field(default_factory=lambda: dict(DEFAULT_WEIGHTS),
                          sa_column=Column(JSON))
    include_companies: List[str] = Field(default_factory=list,
                                         sa_column=Column(JSON))
    exclude_companies: List[str] = Field(default_factory=list,
                                         sa_column=Column(JSON))
    targeting_notes: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class JobPosting(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
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


class ApplicationPackage(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
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


_connect_args = {"check_same_thread": False} if _IS_SQLITE else {}
engine = create_engine(DATABASE_URL, echo=False,
                       connect_args=_connect_args)


def _ensure_additive_columns():
    """create_all() never ALTERs an existing table, so a DB created
    before a new (nullable/defaulted) column was added would be missing
    it. This applies only safe, additive `ADD COLUMN`s for columns that
    already exist in the model - no drops, no type changes, no data
    loss. Postgres production still goes through Alembic; this keeps the
    zero-config SQLite path and any pre-existing DB self-healing."""
    from sqlalchemy import inspect as _inspect, text as _sql
    additive = {
        "jobposting": {
            "structured_requirements": "JSON" if not _IS_SQLITE
            else "TEXT",
        },
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


def init_db():
    SQLModel.metadata.create_all(engine)
    _ensure_additive_columns()


def get_session():
    with Session(engine) as s:
        yield s


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


def claim_provenance(session, claim):
    if claim.approval_status in (ApprovalStatus.rejected,
                                 ApprovalStatus.do_not_use):
        return ProvenanceCategory.unsupported
    src = session.get(Source, claim.source_id)
    if src is None:
        return ProvenanceCategory.unsupported
    return provenance_for_source(src.source_type)


# ===========================================================================
# Job import
# ===========================================================================
_TITLE_HINT = re.compile(r"(?im)^\s*(?:title|role|position)\s*[:\-]\s*(.+)$")
_COMPANY_HINT = re.compile(
    r"(?im)^\s*(?:company|organization|employer)\s*[:\-]\s*(.+)$")
_LOCATION_HINT = re.compile(r"(?im)^\s*(?:location|based in)\s*[:\-]\s*(.+)$")
_SALARY = re.compile(
    r"\$\s?(\d[\d,]*)(?:\s?[kK])?\s?(?:-|to|\u2013|\u2014)\s?\$?\s?(\d[\d,]*)"
    r"(?:\s?[kK])?")
_REQ_LINE = re.compile(r"^\s*[\u2022\-\*\u00b7]\s+(.{6,200})$")


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


def _first(pattern, text):
    m = pattern.search(text)
    return m.group(1).strip() if m else None


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


def _structured_requirements(text):
    """Split requirements into must-have vs nice-to-have and pull out
    seniority / skills / domains. Purely additive: the flat list from
    _extract_requirements is still produced and stored separately."""
    must, nice = [], []
    section = None  # None | "must" | "nice"
    for raw in (text or "").splitlines():
        low = raw.strip().lower()
        if low:
            if any(h in low for h in _NICE_HDR) and len(low) < 60:
                section = "nice"
            elif any(h in low for h in _MUST_HDR) and len(low) < 60:
                section = "must"
        m = _REQ_LINE.match(raw)
        if not m:
            continue
        item = m.group(1).strip()
        if not item or len(item) < 6:
            continue
        bucket = nice if (section == "nice"
                          or _NICE_INLINE.search(item)) else must
        if item not in must and item not in nice:
            bucket.append(item)
    blob = (text or "").lower()
    yrs = [int(y) for y in _YEARS.findall(blob)]
    seniority = _seniority_rank(blob)
    skills = sorted(set(_extract_skills(blob)))
    domains = sorted(set(_domains_in(blob)))
    return {
        "must_have": must[:20],
        "nice_to_have": nice[:20],
        "min_years": min(yrs) if yrs else None,
        "seniority_rank": seniority,
        "skills": skills,
        "domains": domains,
    }


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


def _find_duplicate(session, company, title, source_url):
    key = _dedupe_key(company, title, source_url)
    for j in session.exec(select(JobPosting)).all():
        if _dedupe_key(j.company, j.title, j.source_url) == key:
            return j
    return None


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


def import_job(description_text, source_url=None, title=None, company=None,
               source="manual_import"):
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


# ===========================================================================
# Schemas + Delivery 1 routers
# ===========================================================================
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


class StrategyRead(StrategyUpsert):
    id: str
    is_active: bool
    weights: dict
    updated_at: datetime


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


sources_router = APIRouter(prefix="/api/sources", tags=["sources"])
claims_router = APIRouter(prefix="/api/claims", tags=["claims"])
strategy_router = APIRouter(prefix="/api/strategy", tags=["strategy"])
jobs_router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _source_read(session, s):
    cnt = len(session.exec(
        select(ProfileClaim).where(ProfileClaim.source_id == s.id)).all())
    return SourceRead(
        id=s.id, source_type=s.source_type, label=s.label,
        filename=s.filename, extracted_text=s.extracted_text,
        parse_meta=s.parse_meta or {}, created_at=s.created_at,
        claim_count=cnt)


def claim_read(session, c):
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


@sources_router.get("", response_model=List[SourceRead])
def list_sources(session: Session = Depends(get_session)):
    rows = session.exec(
        select(Source).order_by(Source.created_at.desc())).all()
    return [_source_read(session, s) for s in rows]


@sources_router.post("", response_model=SourceRead, status_code=201)
def create_source(body: SourceCreate,
                   session: Session = Depends(get_session)):
    src = Source(source_type=body.source_type, label=body.label,
                 filename=body.filename, raw_text=body.raw_text,
                 extracted_text=body.raw_text,
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
        filename=file.filename, raw_text=result.text,
        extracted_text=result.text, parse_meta=result.meta)
    session.add(src)
    session.commit()
    session.refresh(src)
    extract_claims(session, src)
    return _source_read(session, src)


@sources_router.delete("/{source_id}", status_code=204)
def delete_source(source_id: str,
                  session: Session = Depends(get_session)):
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


@claims_router.get("", response_model=List[ClaimRead])
def list_claims(source_id: Optional[str] = None,
                session: Session = Depends(get_session)):
    q = select(ProfileClaim)
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


def _active_strategy(session):
    strat = session.exec(select(Strategy).where(
        Strategy.is_active == True)).first()  # noqa: E712
    if not strat:
        strat = Strategy()
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
        targeting_notes=s.targeting_notes, updated_at=s.updated_at)


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
    s.updated_at = _now()
    session.add(s)
    session.commit()
    session.refresh(s)
    return _strategy_read(s)


def _job_read(j, deduplicated=False):
    return JobRead(
        id=j.id, title=j.title, company=j.company, location=j.location,
        work_mode=j.work_mode, salary_min=j.salary_min,
        salary_max=j.salary_max, source=j.source, source_url=j.source_url,
        description_text=j.description_text, requirements=j.requirements,
        structured_requirements=j.structured_requirements or {},
        is_archived=j.is_archived, deduplicated=deduplicated,
        posted_at=j.posted_at, imported_at=j.imported_at)


@jobs_router.get("", response_model=List[JobRead])
def list_jobs(session: Session = Depends(get_session)):
    rows = session.exec(select(JobPosting).where(
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
    try:
        text = fetch_url_text(body.url)
    except UrlFetchError as e:
        raise HTTPException(422, str(e))
    job = import_job(text, source_url=body.url, title=body.title,
                     company=body.company, source="url_import")
    dup = _find_duplicate(session, job.company, job.title, job.source_url)
    if dup is not None:
        response.status_code = 200
        return _job_read(dup, deduplicated=True)
    session.add(job)
    session.commit()
    session.refresh(job)
    return _job_read(job)


@jobs_router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: str, session: Session = Depends(get_session)):
    j = session.get(JobPosting, job_id)
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
    init_db()
    if SEED_ON_STARTUP:
        seed()
        seed_job_sources()
        seed_packages()
    yield


app = FastAPI(title="Aptiro API", version="0.5.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"], allow_headers=["*"])
app.include_router(sources_router)
app.include_router(claims_router)
app.include_router(strategy_router)
app.include_router(jobs_router)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "Aptiro", "delivery": 4,
            "slice": "trust-export", "phase": 2,
            "providers": {"ai": AI_PROVIDER, "job": JOB_PROVIDER,
                          "search": SEARCH_PROVIDER,
                          "notification": NOTIFICATION_PROVIDER,
                          "embedding":
                              embeddings.active_embedding_provider_name()},
            "ingestion_formats": sorted(ingestion.SUPPORTED),
            "export_formats": exporting.FORMATS,
            "job_import": ["paste", "url"],
            "semantic_signal": {
                "provider": embeddings.active_embedding_provider_name(),
                "affects_score": False}}


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
            posted_at="2026-05-01"))
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


def score_job(session, job, strategy):
    w = strategy.weights or dict(DEFAULT_WEIGHTS)
    prof = _candidate_profile(session)
    job_low = " ".join([job.title or "", job.description_text or "",
                        " ".join(job.requirements or [])]).lower()
    comps = []

    def comp(key, label, frac, detail, ev=None):
        weight = _weight(w, key)
        earned = round(weight * max(0.0, min(1.0, frac)), 2)
        evidence = []
        seen = set()
        for cid in (ev or []):
            if cid in seen or cid not in prof["by_claim"]:
                continue
            seen.add(cid)
            evidence.append({"claim_id": cid,
                             "snippet": prof["by_claim"][cid]})
            if len(evidence) >= 6:
                break
        comps.append({"key": key, "label": label, "weight": weight,
                      "earned": earned, "detail": detail,
                      "evidence": evidence})

    inc = [c.lower() for c in (strategy.include_companies or [])]
    exc = [c.lower() for c in (strategy.exclude_companies or [])]
    excluded = job.company.lower() in exc

    targets = [t.lower() for t in (strategy.target_roles or [])]
    title_low = (job.title or "").lower()
    if targets:
        best = max((len(_tokens(t) & _tokens(title_low)) /
                    max(1, len(_tokens(t))) for t in targets), default=0)
        comp("role_alignment", "Role alignment", best,
             ("Title overlaps your target role(s)." if best > 0.4 else
              "Limited overlap with your target role(s)."))
    else:
        comp("role_alignment", "Role alignment", 0.5,
             "No target roles set - neutral credit.")

    job_sr = _seniority_rank(job_low)
    diff = abs(job_sr - prof["seniority"])
    comp("seniority_alignment", "Seniority alignment",
         max(0.0, 1.0 - diff / 4.0),
         "Role seniority ~%d vs your ~%d." % (job_sr, prof["seniority"]),
         prof["senior_src"])

    job_sk = set(_extract_skills(job_low))
    prof_sk = set(prof["skills"])
    matched = sorted(job_sk & prof_sk)
    skill_ev = []
    for sk in matched:
        skill_ev.extend(prof["skill_src"].get(sk, []))
    comp("core_skills", "Core skills coverage",
         (len(matched) / len(job_sk)) if job_sk else 0.5,
         ("Matched: %s." % ", ".join(matched)) if matched else
         "No overlapping skills detected from approved claims.",
         skill_ev)

    jd = _domains_in(job_low)
    shared = [d for d in prof["domains"] if d in jd]
    dom_ev = []
    for d in shared:
        dom_ev.extend(prof["domain_src"].get(d, []))
    comp("domain", "Domain fit",
         (len(shared) / len(jd)) if jd else 0.5,
         ("Shared domain(s): %s." % ", ".join(shared)) if shared else
         (("Posting domain(s) %s not in your profile." % ", ".join(jd))
          if jd else "No clear domain signal - neutral credit."),
         dom_ev)

    job_lead = any(k in job_low for k in _LEADERSHIP) or job_sr >= 4
    if prof["leadership"] and job_lead:
        lf, ld = 1.0, "Your leadership scope matches a leadership role."
    elif prof["leadership"]:
        lf, ld = 0.6, "You show leadership scope; role is more IC."
    elif job_lead:
        lf, ld = 0.3, "Role expects leadership; limited evidence."
    else:
        lf, ld = 0.5, "Neither side emphasizes leadership scope."
    comp("leadership_scope", "Leadership scope", lf, ld,
         prof["lead_src"])

    cand_ai = [t for t in _AI_TERMS if _has_term(prof["text"], t)]
    job_ai = [t for t in _AI_TERMS if _has_term(job_low, t)]
    shared_ai = sorted({t for t in cand_ai if t in job_ai})
    ai_ev = []
    for t in shared_ai:
        ai_ev.extend(prof["ai_src"].get(t, []))
    comp("ai_technical", "AI / technical depth",
         min(1.0, len(shared_ai) / 3.0) if job_ai else 0.4,
         ("Shared AI/technical signals: %s." % ", ".join(shared_ai[:6]))
         if shared_ai else
         ("Role is AI/technical but profile shows little overlap."
          if job_ai else "Role is not strongly AI/technical."),
         ai_ev)

    comp("evidence_strength", "Evidence strength", prof["evidence"],
         ("Backed by %d usable claim(s), %d grounded resume truth; "
          "avg evidence quality %.2f."
          % (prof["n"], prof["grounded"], prof["evidence"]))
         if prof["n"] else
         "No usable approved claims yet - approve claims in Profile "
         "Vault.",
         prof["all_ids"])

    pref = 1.0
    pbits = []
    if strategy.work_mode != WorkMode.any and \
            job.work_mode != strategy.work_mode:
        pref -= 0.4
        pbits.append("work mode differs")
    if strategy.salary_min and job.salary_max and \
            job.salary_max < strategy.salary_min:
        pref -= 0.4
        pbits.append("below salary floor")
    comp("preferences", "Preference match", max(0.0, pref),
         ("Mismatch: %s." % ", ".join(pbits)) if pbits else
         "Work mode and salary fit your preferences.")

    notes_kw = [t for t in _tokens(strategy.targeting_notes or "")
                if len(t) > 3 and t in job_low]
    if excluded:
        comp("strategy_boost", "Strategy boost", 0.0,
             "%s is on your exclude list." % job.company)
    elif any(cc == title_low or cc in job.company.lower() for cc in inc):
        comp("strategy_boost", "Strategy boost", 1.0,
             "%s is on your include/priority list." % job.company)
    elif notes_kw:
        comp("strategy_boost", "Strategy boost",
             min(1.0, len(notes_kw) / 3.0),
             "Targeting-note keywords present: %s."
             % ", ".join(sorted(set(notes_kw))[:5]))
    else:
        comp("strategy_boost", "Strategy boost", 0.3,
             "No include-list or targeting-note signal; base credit.")

    earned = round(sum(c["earned"] for c in comps), 2)
    maxp = sum(c["weight"] for c in comps) or 1
    pct = 0 if excluded else max(0, min(100, round(100 * earned / maxp)))
    prof_tok = _tokens(prof["text"]) | _tokens(" ".join(prof["skills"]))
    missing = [r for r in (job.requirements or [])
               if _tokens(r) and not (_tokens(r) & prof_tok)]
    if excluded:
        summary = ("Excluded: %s is on your exclude list. Shown for "
                   "transparency at score 0." % job.company)
    else:
        band = ("strong" if pct >= 75 else
                "moderate" if pct >= 50 else "weak")
        top = max(comps, key=lambda c: c["earned"])
        summary = "%d/100 - %s match. Strongest on: %s." % (
            pct, band, top["label"])
    # --- Secondary semantic signal (Phase 2) ---------------------------
    # A clearly-labelled SECONDARY indicator only. It is computed from a
    # deterministic mock embedding by default and NEVER feeds `pct` or
    # changes ranking - the weighted score above stays the single source
    # of truth. It exists so the UI can show whether an opaque vector
    # model "agrees" with the explainable score.
    ep = embeddings.get_embedding_provider()
    prof_vec = ep.embed(" ".join(
        [prof["text"], " ".join(prof["skills"])]))
    job_vec = ep.embed(" ".join(
        [job.title or "", job.description_text or "",
         " ".join(job.requirements or [])]))
    sim = embeddings.cosine(prof_vec, job_vec)
    semantic = {
        "provider": ep.name,
        "similarity": sim,
        "label": "secondary signal",
        "affects_score": False,
        "note": ("Embedding cosine similarity between your approved "
                 "evidence and this posting. Secondary only - it does "
                 "not change the %d/100 score or the ranking." % pct),
        "agreement": ("aligned" if abs(sim * 100 - pct) <= 20 else
                      "diverges"),
    }

    return {"job_id": job.id, "score": pct, "earned_points": earned,
            "max_points": maxp, "components": comps,
            "matched_skills": matched, "missing_requirements": missing[:8],
            "excluded": excluded, "summary": summary,
            "structured_requirements": (
                job.structured_requirements or {}),
            "semantic": semantic}


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
        title=job.title, company=job.company,
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
    rows = session.exec(select(ApplicationPackage).order_by(
        ApplicationPackage.created_at.desc())).all()
    return [_package_out(session, p) for p in rows]


@packages_router.post("", response_model=PackageOut, status_code=201)
def create_package(body: PackageCreate,
                   session: Session = Depends(get_session)):
    job = session.get(JobPosting, body.job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    pkg = build_package(session, job, _active_strategy(session))
    return _package_out(session, pkg)


@packages_router.get("/{pkg_id}", response_model=PackageOut)
def get_package(pkg_id: str, session: Session = Depends(get_session)):
    pkg = session.get(ApplicationPackage, pkg_id)
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
    if format not in exporting.FORMATS:
        raise HTTPException(
            400, "Unsupported format. Choose one of: %s"
            % ", ".join(exporting.FORMATS))
    if artifact not in ("resume", "cover_letter", "both"):
        raise HTTPException(400, "artifact must be resume|cover_letter|both")
    model = _export_model(session, pkg, include_unsupported)
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
            ApplySession, NotificationPreview, AgentCritique, AgentRun,
            PackageBullet, ApplicationPackage, SourceRef, ProfileClaim,
            JobPosting, Strategy, Source,
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


def export_bundle(session):
    bundle = {"app": "Aptiro", "delivery": 4,
              "exported_at": _now().isoformat(), "data": {},
              "counts": {}}
    for m in _all_models():
        rows = session.exec(select(m)).all()
        key = m.__tablename__
        bundle["data"][key] = [_row_dict(r) for r in rows]
        bundle["counts"][key] = len(rows)
    return bundle


def wipe_all(session):
    removed = {}
    for m in _all_models():
        rows = session.exec(select(m)).all()
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
