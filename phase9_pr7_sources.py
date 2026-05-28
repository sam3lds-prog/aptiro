#!/usr/bin/env python3
"""
Aptiro — Phase 9 PR-7: Extract modules/sources/__init__.py

Names moved:
    Source          SQLModel (table=True)
    SourceRef       SQLModel (table=True)
    SourceCreate    Pydantic schema
    SourceRead      Pydantic schema
    SourceRefRead   Pydantic schema
    _source_read    helper function
    sources_router  APIRouter + 4 endpoints (list, create, upload, delete)

Cross-module dependency handling
─────────────────────────────────
  SourceType      — module-level import from app.legacy. Safe: SourceType
                    (enum) is defined EARLY in legacy.py, before the
                    modules/sources import line. Python's partial-module
                    mechanism makes it available when modules/sources loads.

  ProfileClaim    — NOT extracted until PR-8. Two sub-cases:
    Relationships — use string literals "ProfileClaim" in both Source.claims
                    and SourceRef.claim so SQLAlchemy resolves them lazily
                    at mapper-init, not at class-definition time.
    Runtime uses  — lazy `from app.legacy import ProfileClaim` INSIDE the
                    function body. All modules are fully loaded by first
                    request, so there is no AttributeError at runtime.

  extract_claims  — still in legacy.py until PR-8. Lazy import inside
                    create_source and upload_source endpoint bodies.

  ingestion       — standalone backend/ingestion.py, no circular risk.
                    Module-level import.

NOT moved:
    app.include_router(sources_router)  — references FastAPI app; stays
                                          in legacy until PR-16 cleanup.
    claims_router + all claims endpoints — depend on ProfileClaim (PR-8).

Six tight patterns (≤ 2500 chars each — PR-5 lesson):
    P1  class Source(SQLModel, table=True): ...
        Replaced with combined import block + MARKER.
    P2  class SourceRef(SQLModel, table=True): ...
    P3  class SourceCreate / SourceRead / SourceRefRead (contiguous)
    P4  sources_router = APIRouter(...)  — single-line declaration
    P5  def _source_read(session, s): ...
    P6  All 4 source endpoint handlers

P2..P6 are replaced with a single-line comment each.

Run from project root.  Idempotent.

Usage:
    python3 phase9_pr7_sources.py
    python3 phase9_pr7_sources.py --dry-run
"""
import argparse
import pathlib
import py_compile
import re
import sys

parser = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()

ROOT     = pathlib.Path.cwd()
BACKEND  = ROOT / "backend"
LEGACY   = BACKEND / "app" / "legacy.py"
SRC_DIR  = BACKEND / "app" / "modules" / "sources"
SRC_FILE = SRC_DIR / "__init__.py"
MARKER   = "APTIRO_PHASE9_PR7_SOURCES_MARKER"

def fail(msg, code=1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)

for p, label in [(BACKEND, "backend/"), (LEGACY, "backend/app/legacy.py"),
                 (BACKEND/"app"/"db"/"engine.py", "db/engine.py (PR-5)"),
                 (BACKEND/"app"/"modules"/"auth"/"__init__.py", "modules/auth (PR-6)")]:
    if not p.exists():
        fail(f"{label} not found — prerequisite PR not applied yet.")

legacy_src = LEGACY.read_text()
if MARKER in legacy_src:
    print("Phase 9 PR-7 already applied — nothing to do.")
    sys.exit(0)

# ─────────────────────────────────────────────────────────────────────────────
# New module: modules/sources/__init__.py
# ─────────────────────────────────────────────────────────────────────────────
SRC_MODULE = '''\
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
# module level because it is already in legacy\'s namespace when Python
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
'''

# ─────────────────────────────────────────────────────────────────────────────
# Six tight patterns for legacy.py
# ─────────────────────────────────────────────────────────────────────────────

# P1: Source model — stops before ProfileClaim (next model class)
PAT_SOURCE = re.compile(
    r"class Source\(SQLModel, table=True\):.*?(?=\n\nclass ProfileClaim)",
    re.DOTALL,
)

# P2: SourceRef model — stops before DEFAULT_WEIGHTS dict
PAT_SOURCE_REF = re.compile(
    r"class SourceRef\(SQLModel, table=True\):.*?(?=\n\nDEFAULT_WEIGHTS)",
    re.DOTALL,
)

# P3: SourceCreate + SourceRead + SourceRefRead (contiguous in schemas section)
#     Stops before ClaimRead (first non-source schema class)
PAT_SOURCE_SCHEMAS = re.compile(
    r"class SourceCreate\(BaseModel\):.*?(?=\n\nclass ClaimRead)",
    re.DOTALL,
)

# P4: sources_router = APIRouter(...) — single line
PAT_ROUTER_DECL = re.compile(
    r'^sources_router = APIRouter\(prefix="/api/sources"[^\n]*\n',
    re.MULTILINE,
)

# P5: _source_read helper — stops before first @sources_router decorator
PAT_SOURCE_READ_FN = re.compile(
    r"def _source_read\(session, s\):.*?(?=\n\n@sources_router\.get)",
    re.DOTALL,
)

# P6: All 4 source endpoint handlers — stops before first @claims_router
#     Matches @sources_router.get("" ...  (with possible trailing args)
PAT_SOURCE_ENDPOINTS = re.compile(
    r'@sources_router\.get\("".*?(?=\n@claims_router\.get)',
    re.DOTALL,
)

IMPORT_BLOCK = (
    "# " + MARKER + "\n"
    "# Source, SourceRef, schemas, sources_router → modules/sources/ (PR-7)\n"
    "from app.modules.sources import (  # noqa: F401\n"
    "    Source, SourceRef,\n"
    "    SourceCreate, SourceRead, SourceRefRead,\n"
    "    sources_router, _source_read,\n"
    ")\n"
)

# ─────────────────────────────────────────────────────────────────────────────
# Locate and validate every pattern
# ─────────────────────────────────────────────────────────────────────────────
patterns_spec = [
    ("Source model",     PAT_SOURCE,
     ["class Source", "parse_meta", "extracted_text"]),
    ("SourceRef model",  PAT_SOURCE_REF,
     ["class SourceRef", "claim_id", "foreign_key"]),
    ("source schemas",   PAT_SOURCE_SCHEMAS,
     ["SourceCreate", "SourceRead", "SourceRefRead", "claim_count"]),
    ("router decl",      PAT_ROUTER_DECL,
     ["sources_router", "APIRouter"]),
    ("_source_read fn",  PAT_SOURCE_READ_FN,
     ["_source_read", "SourceRead", "claim_count"]),
    ("source endpoints", PAT_SOURCE_ENDPOINTS,
     ["list_sources", "create_source", "upload_source", "delete_source"]),
]

matches = {}
for name, pat, required in patterns_spec:
    m = pat.search(legacy_src)
    if not m:
        fail(
            f"Pattern '{name}' not found in legacy.py.\n"
            f"Run: grep -n '{required[0]}' backend/app/legacy.py"
        )
    missing = [r for r in required if r not in m.group(0)]
    if missing:
        fail(f"Pattern '{name}' matched but missing required names: {missing}")
    size = len(m.group(0))
    if size > 8000:
        fail(
            f"Pattern '{name}' matched {size:,} chars — suspiciously large "
            f"(PR-5 lesson: must be tight).\nFirst 200 chars:\n{m.group(0)[:200]}"
        )
    matches[name] = m
    line_no = legacy_src[:m.start()].count("\n") + 1
    print(f"  ✓ {name:<20}  line ~{line_no:>4}  ({size:>5} chars)")

# Ordering sanity: Source < SourceRef < schemas < endpoints
for earlier, later in [
    ("Source model", "SourceRef model"),
    ("SourceRef model", "source schemas"),
    ("source schemas", "source endpoints"),
]:
    if matches[earlier].start() >= matches[later].start():
        fail(f"Unexpected order: '{earlier}' not before '{later}'")
print("  ✓ ordering confirmed")

if args.dry_run:
    print(f"\n[dry-run] Would write {SRC_FILE.relative_to(ROOT)} ({len(SRC_MODULE):,} chars)")
    print(f"[dry-run] Would apply 6 replacements to {LEGACY.relative_to(ROOT)}")
    print("[dry-run] No files written.")
    sys.exit(0)

# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Write modules/sources/__init__.py
# ─────────────────────────────────────────────────────────────────────────────
SRC_DIR.mkdir(parents=True, exist_ok=True)
print(f"\nWriting {SRC_FILE.relative_to(ROOT)} …")
SRC_FILE.write_text(SRC_MODULE)
print(f"  ✓ {len(SRC_MODULE):,} chars")

# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Patch legacy.py (back-to-front so offsets stay valid)
# ─────────────────────────────────────────────────────────────────────────────
print(f"\nPatching {LEGACY.relative_to(ROOT)} …")

replacements = [
    (matches["source endpoints"].start(), matches["source endpoints"].end(),
     "# source endpoints imported from modules/sources (Phase 9 PR-7)\n",
     "source endpoints"),
    (matches["_source_read fn"].start(),  matches["_source_read fn"].end(),
     "# _source_read imported from modules/sources (Phase 9 PR-7)\n",
     "_source_read fn"),
    (matches["router decl"].start(),      matches["router decl"].end(),
     "# sources_router imported from modules/sources (Phase 9 PR-7)\n",
     "router decl"),
    (matches["source schemas"].start(),   matches["source schemas"].end(),
     "# SourceCreate/Read/RefRead imported from modules/sources (Phase 9 PR-7)\n",
     "source schemas"),
    (matches["SourceRef model"].start(),  matches["SourceRef model"].end(),
     "# SourceRef imported from modules/sources (Phase 9 PR-7)\n",
     "SourceRef model"),
    (matches["Source model"].start(),     matches["Source model"].end(),
     IMPORT_BLOCK,
     "Source model + import block"),
]
replacements.sort(key=lambda t: t[0], reverse=True)

patched = legacy_src
for start, end, repl, label in replacements:
    patched = patched[:start] + repl + patched[end:]
    print(f"  ✓ {label:<32}  ({end - start:>5} → {len(repl):>4} chars)")

LEGACY.write_text(patched)

# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Byte-compile
# ─────────────────────────────────────────────────────────────────────────────
print("\nByte-compiling …")
ok = True
for f in [SRC_FILE, LEGACY]:
    try:
        py_compile.compile(str(f), doraise=True)
        print(f"  ✓ {f.relative_to(BACKEND)}")
    except py_compile.PyCompileError as exc:
        print(f"  ✗ {f.relative_to(BACKEND)}  —  {exc}")
        ok = False
if not ok:
    fail("Compile failed.\nTo undo: git checkout backend/app/legacy.py", code=4)

# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Structural checks
# ─────────────────────────────────────────────────────────────────────────────
print("\nPost-patch checks …")
final = LEGACY.read_text()

checks = [
    ("MARKER present",                MARKER in final),
    ("from app.modules.sources",      "from app.modules.sources import" in final),
    ("Source in import",              "    Source," in final),
    ("SourceRef in import",           "    SourceRef," in final),
    ("sources_router in import",      "    sources_router," in final),
    ("_source_read in import",        "    _source_read," in final),
    ("no class Source(SQLModel",      "class Source(SQLModel, table=True):" not in final),
    ("no class SourceRef(SQLModel",   "class SourceRef(SQLModel, table=True):" not in final),
    ("no class SourceCreate",         "class SourceCreate(BaseModel):" not in final),
    ("no class SourceRead(BaseModel", "class SourceRead(BaseModel):" not in final),
    ("no class SourceRefRead",        "class SourceRefRead(BaseModel):" not in final),
    ("no sources_router = APIRouter", "sources_router = APIRouter(" not in final),
    ("no def list_sources",           "def list_sources(" not in final),
    ("no def _source_read",           "def _source_read(" not in final),
    ("ProfileClaim still there",      "class ProfileClaim(" in final),
    ("claims_router still there",     "claims_router = APIRouter(" in final),
    ("def list_claims still there",   "def list_claims(" in final),
    ("DEFAULT_WEIGHTS still there",   "DEFAULT_WEIGHTS" in final),
    ("AuditEvent still there",        "class AuditEvent(" in final),
    ("ClaimRead still there",         "class ClaimRead(" in final),
    ("app.include_router(sources",    "app.include_router(sources_router)" in final),
    ("_ensure_additive_columns ok",   "def _ensure_additive_columns():" in final),
    ("init_db ok",                    "def init_db():" in final),
]

all_ok = True
for label, result in checks:
    print(f"  {'✓' if result else '✗'} {label}")
    if not result:
        all_ok = False

if not all_ok:
    fail("Structural checks failed.\nTo undo: git checkout backend/app/legacy.py", code=5)

print(f"""
═══════════════════════════════════════════════════════════════════════
Phase 9 PR-7 (modules/sources/) applied.

Files changed:
  backend/app/modules/sources/__init__.py  ← NEW
  backend/app/legacy.py                    ← PATCHED (6 blocks → imports)

Names moved:
  Source, SourceRef
  SourceCreate, SourceRead, SourceRefRead
  sources_router (4 endpoints), _source_read

Cross-module dep strategy:
  SourceType          module-level import from app.legacy (safe)
  ProfileClaim        string forward refs + lazy fn-body imports
  extract_claims      lazy fn-body imports (moves in PR-8)

Still in legacy.py:
  ProfileClaim, claims_router + endpoints   → PR-8
  app.include_router(sources_router)        → PR-16 final cleanup

Run the full test suite:
  cd backend && . .venv/bin/activate && pytest -q

Expected: 218 passed, all green.

To undo:
  git checkout backend/app/legacy.py
  rm backend/app/modules/sources/__init__.py
═══════════════════════════════════════════════════════════════════════
""")
