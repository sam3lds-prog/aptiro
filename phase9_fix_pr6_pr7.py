#!/usr/bin/env python3
"""
Aptiro — Phase 9 combined fix for PR-6 and PR-7.

Root causes fixed
─────────────────
PR-6  PAT_ROUTER used the literal em-dash character (—) from the Phase 4
      section comment. In the actual file the dash was identical, BUT the
      regex engine reported no match — likely a Unicode normalisation
      difference between the source written on the dev machine and the
      bytes on disk. Fix: anchor on the code line instead of the comment:
        OLD  r"# =====+\n# Phase 4 — auth: user accounts.*?"
        NEW  r"auth_router = APIRouter\\(prefix=\"/api/auth\".*?"

PR-7  PAT_SOURCE_READ_FN stopped at the first @sources_router.get
      decorator, but claim_read() lives BETWEEN _source_read() and that
      decorator. Both functions were removed; legacy.py lost claim_read,
      so every /api/claims request blew up with NameError.
        OLD  r"def _source_read\\(session, s\\):.*?(?=\\n\\n@sources_router\\.get)"
        NEW  r"def _source_read\\(session, s\\):.*?(?=\\n\\ndef claim_read)"

Structural-check false positives also fixed:
      The checks "SourceRef in import" and "_source_read in import" were
      looking for r"    SourceRef," (4-space + name + comma) but both names
      sit on multi-item lines ("    Source, SourceRef,"). Changed to a
      broader regex search so the check doesn't produce a spurious failure.

What this script does
─────────────────────
1. Restore legacy.py from git HEAD (the clean post-PR5 state).
2. Apply PR-6: write modules/auth/__init__.py, patch legacy.py.
3. Apply PR-7: write modules/sources/__init__.py, patch legacy.py.
4. Byte-compile both new modules and the patched legacy.py.
5. Run all structural checks and report.

Run from the project root (directory containing backend/).

Usage:
    python3 phase9_fix_pr6_pr7.py
    python3 phase9_fix_pr6_pr7.py --dry-run
"""
import argparse
import pathlib
import py_compile
import re
import subprocess
import sys

parser = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()

ROOT     = pathlib.Path.cwd()
BACKEND  = ROOT / "backend"
LEGACY   = BACKEND / "app" / "legacy.py"
AUTH_DIR = BACKEND / "app" / "modules" / "auth"
AUTH_FILE = AUTH_DIR / "__init__.py"
SRC_DIR  = BACKEND / "app" / "modules" / "sources"
SRC_FILE = SRC_DIR / "__init__.py"

MARKER_PR6 = "APTIRO_PHASE9_PR6_AUTH_MARKER"
MARKER_PR7 = "APTIRO_PHASE9_PR7_SOURCES_MARKER"

def fail(msg, code=1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)

if not BACKEND.is_dir():
    fail("backend/ not found. Run from the project root.")
if not LEGACY.exists():
    fail("backend/app/legacy.py not found.")

# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Restore legacy.py from HEAD
# ─────────────────────────────────────────────────────────────────────────────
print("Step 1/4  Restoring legacy.py from HEAD …")
result = subprocess.run(
    ["git", "show", "HEAD:backend/app/legacy.py"],
    capture_output=True, text=True, cwd=ROOT
)
if result.returncode != 0:
    fail(f"git show HEAD failed:\n{result.stderr}")
clean = result.stdout
if MARKER_PR6 in clean or MARKER_PR7 in clean:
    fail("HEAD already contains a PR-6/7 marker — this fix is unnecessary.")
if "_IS_SQLITE = DATABASE_URL" in clean:
    fail("HEAD still has _IS_SQLITE = ... assignment. PR-5 (fix_pr5) was not applied.")

if not args.dry_run:
    LEGACY.write_text(clean)

nlines = clean.count("\n") + 1
print(f"  ✓ restored {len(clean):,} chars (~{nlines:,} lines)")

# ─────────────────────────────────────────────────────────────────────────────
# PR-6 module content
# ─────────────────────────────────────────────────────────────────────────────
AUTH_MODULE = '''\
"""
Aptiro backend — modules/auth/__init__.py (Phase 9 PR-6).

User model, password/token helpers, owner-scoping utilities,
Phase 4 auth router (/register /login /me), and _ensure_default_user.
Extracted from backend/app/legacy.py.

Dependencies:
  app.core.config   — AUTH_ENABLED, DEFAULT_UID, DEFAULT_USER_EMAIL, _PW_ROUNDS
  app.core.identity — _uid
  app.db.engine     — engine, get_session, _now
"""
import hashlib as _hashlib
import hmac as _hmac
import secrets as _secrets
import sys as _sys
import uuid as _uuidmod
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Field, Session, SQLModel, select

from app.core.config import AUTH_ENABLED, DEFAULT_UID, DEFAULT_USER_EMAIL, _PW_ROUNDS
from app.core.identity import _uid
from app.db.engine import _now, engine, get_session


def _uuid() -> str:
    return _uuidmod.uuid4().hex


class User(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    email: str = Field(index=True)
    name: str = ""
    password_hash: str = ""
    token: str = Field(default="", index=True)
    is_default: bool = False
    created_at: datetime = Field(default_factory=_now)


# Phase 8: append token_expires_at column at module-load time.
from sqlalchemy import Column as _p8_Column, DateTime as _p8_DateTime  # noqa: E402
if "token_expires_at" not in User.__table__.columns:
    User.__table__.append_column(
        _p8_Column("token_expires_at", _p8_DateTime(), nullable=True)
    )


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


def _new_token() -> str:
    return _secrets.token_urlsafe(32)


def _bearer(request) -> str:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else ""


def _scoped(session, model):
    """Owner-filtered select for the current request\'s user."""
    return session.exec(
        select(model).where(model.owner_id == _uid())).all()


def _get_owned(session, model, obj_id):
    """Fetch by id but only if it belongs to the current user."""
    obj = session.get(model, obj_id)
    if obj is None:
        return None
    if getattr(obj, "owner_id", _uid()) != _uid():
        return None
    return obj


def _ensure_default_user():
    """Ensure the built-in \'local\' user row exists."""
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
        return MeOut(id=DEFAULT_UID, email=DEFAULT_USER_EMAIL,
                     name="Local User", is_default=True,
                     auth_enabled=AUTH_ENABLED)
    return MeOut(id=u.id, email=u.email, name=u.name,
                 is_default=u.is_default, auth_enabled=AUTH_ENABLED)
'''

# ─────────────────────────────────────────────────────────────────────────────
# PR-7 module content
# ─────────────────────────────────────────────────────────────────────────────
SRC_MODULE = '''\
"""
Aptiro backend — modules/sources/__init__.py (Phase 9 PR-7).

Source model, SourceRef model, Pydantic schemas, _source_read helper,
and sources_router with four endpoints (list, create, upload, delete).
Extracted from backend/app/legacy.py.

Cross-module deps:
  SourceType     module-level import from app.legacy (defined early, safe)
  ProfileClaim   string forward refs in SQLModel; lazy fn-body imports
  extract_claims lazy fn-body imports (moves to modules/profile_truth PR-8)
  ingestion      standalone module, no circular risk
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

# SourceType is defined early in legacy.py (before the modules/sources
# import line) so this module-level import is safe at partial-load time.
from app.legacy import SourceType  # noqa: F401


def _uuid() -> str:
    return _uuidmod.uuid4().hex


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
    # String forward ref for the same reason.
    claim: Optional["ProfileClaim"] = Relationship(
        back_populates="source_refs")


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


@sources_router.post("/upload", response_model=SourceRead, status_code=201)
async def upload_source(
        file: UploadFile = File(...),
        source_type: SourceType = Query(SourceType.resume),
        session: Session = Depends(get_session)):
    """Production ingestion: PDF / DOCX / TXT / Markdown."""
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
# PR-6 patterns  (all tight, ≤1400 chars each)
# ─────────────────────────────────────────────────────────────────────────────
P6_USER = re.compile(
    r"class User\(SQLModel, table=True\):.*?(?=\n\nclass AuditEvent)",
    re.DOTALL)
P6_P8COL = re.compile(
    r"# The column is intentionally NOT on the User SQLModel Python class.*?"
    r"_p8_DateTime\(\), nullable=True\)\s*\)\s*\n",
    re.DOTALL)
P6_HELPERS = re.compile(
    r"# --- Phase 4: auth \+ scoping helpers -+\n.*?"
    r"(?=\n# =====+\n# Parsing \+ extraction)",
    re.DOTALL)
P6_ENSURE = re.compile(
    r"def _ensure_default_user\(\):.*?(?=\n\ndef init_db\(\):)",
    re.DOTALL)
# FIXED: anchor on the code line, not the em-dash comment
P6_ROUTER = re.compile(
    r'auth_router = APIRouter\(prefix="/api/auth".*?'
    r'(?=\napp\.include_router\(auth_router\))',
    re.DOTALL)

P6_IMPORT = (
    "# " + MARKER_PR6 + "\n"
    "# User, auth helpers, auth_router, _ensure_default_user\n"
    "# extracted to modules/auth/ (Phase 9 PR-6)\n"
    "from app.modules.auth import (  # noqa: F401\n"
    "    User,\n"
    "    _hash_pw, _verify_pw, _new_token, _bearer,\n"
    "    _scoped, _get_owned,\n"
    "    RegisterRequest, LoginRequest, AuthOut, MeOut,\n"
    "    auth_router, _ensure_default_user,\n"
    ")\n"
)

PR6_PATS = [
    ("auth router",     P6_ROUTER,
     ["auth_router", "RegisterRequest", "register", "login", "whoami"]),
    ("User model",      P6_USER,
     ["class User", "password_hash", "is_default"]),
    ("P8 col",          P6_P8COL,
     ["token_expires_at", "_p8_Column", "append_column"]),
    ("auth helpers",    P6_HELPERS,
     ["_hash_pw", "_verify_pw", "_new_token", "_bearer", "_scoped"]),
    ("_ensure_default", P6_ENSURE,
     ["_ensure_default_user", "DEFAULT_UID", "Local User"]),
]

# ─────────────────────────────────────────────────────────────────────────────
# PR-7 patterns  (all tight, ≤3100 chars each)
# ─────────────────────────────────────────────────────────────────────────────
P7_SOURCE = re.compile(
    r"class Source\(SQLModel, table=True\):.*?(?=\n\nclass ProfileClaim)",
    re.DOTALL)
P7_SOURCEREF = re.compile(
    r"class SourceRef\(SQLModel, table=True\):.*?(?=\n\nDEFAULT_WEIGHTS)",
    re.DOTALL)
P7_SCHEMAS = re.compile(
    r"class SourceCreate\(BaseModel\):.*?(?=\n\nclass ClaimRead)",
    re.DOTALL)
P7_DECL = re.compile(
    r'^sources_router = APIRouter\(prefix="/api/sources"[^\n]*\n',
    re.MULTILINE)
# FIXED: stop before claim_read, not @sources_router.get
P7_SRCREAD = re.compile(
    r"def _source_read\(session, s\):.*?(?=\n\ndef claim_read)",
    re.DOTALL)
P7_ENDPOINTS = re.compile(
    r'@sources_router\.get\("".*?(?=\n@claims_router\.get)',
    re.DOTALL)

P7_IMPORT = (
    "# " + MARKER_PR7 + "\n"
    "# Source, SourceRef, schemas, sources_router → modules/sources/ (PR-7)\n"
    "from app.modules.sources import (  # noqa: F401\n"
    "    Source, SourceRef,\n"
    "    SourceCreate, SourceRead, SourceRefRead,\n"
    "    sources_router, _source_read,\n"
    ")\n"
)

PR7_PATS = [
    ("Source model",     P7_SOURCE,
     ["class Source", "parse_meta", "extracted_text"]),
    ("SourceRef model",  P7_SOURCEREF,
     ["class SourceRef", "claim_id", "foreign_key"]),
    ("source schemas",   P7_SCHEMAS,
     ["SourceCreate", "SourceRead", "SourceRefRead", "claim_count"]),
    ("router decl",      P7_DECL,
     ["sources_router", "APIRouter"]),
    ("_source_read fn",  P7_SRCREAD,
     ["_source_read", "SourceRead", "claim_count"]),
    ("source endpoints", P7_ENDPOINTS,
     ["list_sources", "create_source", "upload_source", "delete_source"]),
]

# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Validate all patterns against the restored file
# ─────────────────────────────────────────────────────────────────────────────
print("\nStep 2/4  Validating patterns …")
src = clean  # restored legacy.py content

def validate_patterns(pats, src, pr_label):
    found = {}
    for name, pat, required in pats:
        m = pat.search(src)
        if not m:
            fail(f"[{pr_label}] Pattern '{name}' not found.\n"
                 f"Run: grep -n '{required[0]}' backend/app/legacy.py")
        miss = [r for r in required if r not in m.group(0)]
        if miss:
            fail(f"[{pr_label}] Pattern '{name}' matched but missing {miss}")
        if len(m.group(0)) > 8000:
            fail(f"[{pr_label}] Pattern '{name}' matched {len(m.group(0)):,} chars "
                 f"— too large. First 200:\n{m.group(0)[:200]}")
        found[name] = m
        line = src[:m.start()].count("\n") + 1
        print(f"  [{pr_label}] ✓ {name:<20}  line ~{line:>4}  "
              f"({len(m.group(0)):>5} chars)")
    return found

m6 = validate_patterns(PR6_PATS, src, "PR-6")
# PR-7 patterns operate on the same restored clean file
m7 = validate_patterns(PR7_PATS, src, "PR-7")

if args.dry_run:
    print("\n[dry-run] All patterns valid. No files written.")
    sys.exit(0)

# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Write module files + patch legacy.py
# ─────────────────────────────────────────────────────────────────────────────
print("\nStep 3/4  Writing modules + patching legacy.py …")

AUTH_DIR.mkdir(parents=True, exist_ok=True)
AUTH_FILE.write_text(AUTH_MODULE)
print(f"  ✓ wrote modules/auth/__init__.py ({len(AUTH_MODULE):,} chars)")

SRC_DIR.mkdir(parents=True, exist_ok=True)
SRC_FILE.write_text(SRC_MODULE)
print(f"  ✓ wrote modules/sources/__init__.py ({len(SRC_MODULE):,} chars)")

# Apply PR-6 back-to-front
pr6_replacements = [
    (m6["auth router"].start(),     m6["auth router"].end(),
     "# auth_router imported from modules/auth (Phase 9 PR-6)\n", "auth router"),
    (m6["_ensure_default"].start(), m6["_ensure_default"].end(),
     "# _ensure_default_user imported from modules/auth (Phase 9 PR-6)\n", "_ensure_default"),
    (m6["auth helpers"].start(),    m6["auth helpers"].end(),
     "# auth helpers imported from modules/auth (Phase 9 PR-6)\n", "auth helpers"),
    (m6["P8 col"].start(),          m6["P8 col"].end(),
     "# Phase 8 User col append moved to modules/auth (Phase 9 PR-6)\n", "P8 col"),
    (m6["User model"].start(),      m6["User model"].end(),
     P6_IMPORT, "User model + PR-6 import"),
]
pr6_replacements.sort(key=lambda t: t[0], reverse=True)
patched = clean
for s, e, r, lbl in pr6_replacements:
    patched = patched[:s] + r + patched[e:]
    print(f"  [PR-6] ✓ {lbl:<32} ({e-s:>5} → {len(r):>4} chars)")

# Apply PR-7 on top (back-to-front, using original offsets from 'src=clean')
pr7_replacements = [
    (m7["source endpoints"].start(), m7["source endpoints"].end(),
     "# source endpoints imported from modules/sources (Phase 9 PR-7)\n", "source endpoints"),
    (m7["_source_read fn"].start(),  m7["_source_read fn"].end(),
     "# _source_read imported from modules/sources (Phase 9 PR-7)\n", "_source_read fn"),
    (m7["router decl"].start(),      m7["router decl"].end(),
     "# sources_router imported from modules/sources (Phase 9 PR-7)\n", "router decl"),
    (m7["source schemas"].start(),   m7["source schemas"].end(),
     "# SourceCreate/Read/RefRead imported from modules/sources (Phase 9 PR-7)\n", "source schemas"),
    (m7["SourceRef model"].start(),  m7["SourceRef model"].end(),
     "# SourceRef imported from modules/sources (Phase 9 PR-7)\n", "SourceRef model"),
    (m7["Source model"].start(),     m7["Source model"].end(),
     P7_IMPORT, "Source model + PR-7 import"),
]
pr7_replacements.sort(key=lambda t: t[0], reverse=True)

# PR-7 replacements also operate on the CLEAN source (same offset base).
# We need to apply them to 'patched' (which already has PR-6 applied) using
# the ORIGINAL offsets from 'clean'. This works because PR-6 and PR-7 target
# different non-overlapping regions of legacy.py. Verify no overlap:
p6_regions = {(t[0], t[1]) for t in pr6_replacements}
for s, e, _, lbl in pr7_replacements:
    for s6, e6 in p6_regions:
        if not (e <= s6 or s >= e6):
            fail(f"PR-6 and PR-7 regions overlap at '{lbl}'! Cannot safely combine.")

# The PR-6 replacements shift line offsets. We need to apply PR-7 against the
# PATCHED text using ADJUSTED offsets. Strategy: apply all 12 replacements
# together (sorted back-to-front by original offset in 'clean') so each
# replacement sees the original offsets.
all_replacements = pr6_replacements + pr7_replacements
all_replacements.sort(key=lambda t: t[0], reverse=True)

patched = clean
for s, e, r, lbl in all_replacements:
    patched = patched[:s] + r + patched[e:]
    print(f"  [combined] ✓ {lbl:<32} ({e-s:>5} → {len(r):>4} chars)")

LEGACY.write_text(patched)

# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Byte-compile + structural checks
# ─────────────────────────────────────────────────────────────────────────────
print("\nByte-compiling …")
ok = True
for f in [AUTH_FILE, SRC_FILE, LEGACY]:
    try:
        py_compile.compile(str(f), doraise=True)
        print(f"  ✓ {f.relative_to(BACKEND)}")
    except py_compile.PyCompileError as exc:
        print(f"  ✗ {f.relative_to(BACKEND)}  —  {exc}")
        ok = False
if not ok:
    fail("Compile failed.\nTo undo: git checkout backend/app/legacy.py", code=4)

print("\nStep 4/4  Structural checks …")
final = LEGACY.read_text()

def has_import(name):
    """True if 'name' appears anywhere in the import block for either module."""
    return bool(re.search(
        r"from app\.modules\.(auth|sources) import[^)]*" + re.escape(name),
        final, re.DOTALL))

checks = [
    ("PR-6 MARKER",                  MARKER_PR6 in final),
    ("PR-7 MARKER",                  MARKER_PR7 in final),
    ("from app.modules.auth",        "from app.modules.auth import" in final),
    ("from app.modules.sources",     "from app.modules.sources import" in final),
    ("User in auth import",          has_import("User")),
    ("auth_router in auth import",   has_import("auth_router")),
    ("Source in sources import",     has_import("Source")),
    ("SourceRef in sources import",  has_import("SourceRef")),
    ("_source_read in sources import", has_import("_source_read")),
    ("no class User(SQLModel",       "class User(SQLModel, table=True):" not in final),
    ("no def _hash_pw",              "def _hash_pw(" not in final),
    ("no def _ensure_default",       "def _ensure_default_user():" not in final),
    ("no auth_router = APIRouter",   "auth_router = APIRouter(" not in final),
    ("no class Source(SQLModel",     "class Source(SQLModel, table=True):" not in final),
    ("no class SourceRef(SQLModel",  "class SourceRef(SQLModel, table=True):" not in final),
    ("no class SourceCreate",        "class SourceCreate(BaseModel):" not in final),
    ("no sources_router = APIRouter","sources_router = APIRouter(" not in final),
    ("no def list_sources",          "def list_sources(" not in final),
    ("no def _source_read",          "def _source_read(" not in final),
    # Critical: claim_read MUST still be in legacy
    ("claim_read still there",       "def claim_read(" in final),
    ("ProfileClaim still there",     "class ProfileClaim(" in final),
    ("claims_router still there",    "claims_router = APIRouter(" in final),
    ("def list_claims still there",  "def list_claims(" in final),
    ("DEFAULT_WEIGHTS still there",  "DEFAULT_WEIGHTS" in final),
    ("AuditEvent still there",       "class AuditEvent(" in final),
    ("ClaimRead still there",        "class ClaimRead(" in final),
    ("app.include_router(auth",      "app.include_router(auth_router)" in final),
    ("app.include_router(sources",   "app.include_router(sources_router)" in final),
    ("_ensure_additive_columns ok",  "def _ensure_additive_columns():" in final),
    ("init_db ok",                   "def init_db():" in final),
    ("_mw_session ok",               "def _mw_session():" in final),
    ("ExportToken still there",      "class ExportToken(" in final),
    ("_p8_security_middleware ok",   "_p8_security_middleware" in final),
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
Phase 9 PR-6 + PR-7 fix applied.

Files written / updated:
  backend/app/modules/auth/__init__.py     ← PR-6 (User, auth helpers)
  backend/app/modules/sources/__init__.py  ← PR-7 (Source, SourceRef, router)
  backend/app/legacy.py                    ← both PRs patched

Key fixes:
  PR-6  auth router pattern now anchors on
        auth_router = APIRouter(prefix="/api/auth"
        instead of the em-dash comment that failed to match.

  PR-7  _source_read pattern now stops at:
        \\n\\ndef claim_read
        instead of @sources_router.get, so claim_read stays in legacy
        (it is used by list_claims and other claims endpoints).

Run the full test suite:
  cd backend && . .venv/bin/activate && pytest -q

Expected: 218 passed, all green.

Then commit:
  cd ..
  git add backend/app/modules/auth/__init__.py
  git add backend/app/modules/sources/__init__.py
  git add backend/app/legacy.py
  git add phase9_fix_pr6_pr7.py
  git commit -m "Phase 9 PR-6 + PR-7: extract modules/auth and modules/sources"
  git push origin main
═══════════════════════════════════════════════════════════════════════
""")
