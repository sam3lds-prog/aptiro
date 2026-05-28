#!/usr/bin/env python3
"""
Aptiro — Phase 9 PR-6: Extract modules/auth/__init__.py

Moves auth-related code out of backend/app/legacy.py into
backend/app/modules/auth/__init__.py.

Names moved:
    User                  SQLModel (table=True)
    _hash_pw              password hashing (pbkdf2-hmac-sha256)
    _verify_pw            password verification
    _new_token            bearer token generation
    _bearer               extract bearer token from request headers
    _scoped               owner-filtered select (Phase 4 scoping)
    _get_owned            fetch-by-id with owner check
    RegisterRequest       Pydantic
    LoginRequest          Pydantic
    AuthOut               Pydantic
    MeOut                 Pydantic
    auth_router           APIRouter — /register /login /me
    _ensure_default_user  seeds the built-in 'local' user row

Phase 8 NOT moved (all reference the FastAPI `app` object or stay
coupled to legacy.py middlewares for now):
    ExportToken           move with packages (PR-12)
    _auth8_router         move after ExportToken
    _p8_token_expires     used by _p8_security_middleware in legacy
    _p8_set_token_expiry  same
    _p8_ensure_columns    registered via @app.on_event in legacy
    _p8_security_middleware registered via @app.middleware in legacy
    _p8_on_startup        registered via @app.on_event in legacy
    _p8_rate_ok + consts  used by security middleware in legacy

Five tight individual patterns in legacy.py (lesson from PR-5 — no
large spanning regexes):
    P1  class User(SQLModel, table=True): ...
        → replaced with combined import block + MARKER
    P2  Phase 8 User column append (token_expires_at)
        → removed (runs inside modules/auth at import time)
    P3  # --- Phase 4: auth + scoping helpers --- ... _get_owned
        → removed (imported from modules/auth)
    P4  def _ensure_default_user(): ...
        → removed (imported from modules/auth)
    P5  # Phase 4 — auth: user accounts ... whoami handler
        → removed (imported from modules/auth)
        NOTE: app.include_router(auth_router) stays in legacy — it
              references the `app` FastAPI instance, so it can't move
              until the final main.py cleanup.

Run from the project root (directory containing backend/).
Idempotent — safe to re-run.

Usage:
    python3 phase9_pr6_auth.py
    python3 phase9_pr6_auth.py --dry-run
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
AUTH_DIR = BACKEND / "app" / "modules" / "auth"
AUTH_FILE = AUTH_DIR / "__init__.py"
CFG_FILE  = BACKEND / "app" / "core" / "config.py"
ENG_FILE  = BACKEND / "app" / "db" / "engine.py"
MARKER    = "APTIRO_PHASE9_PR6_AUTH_MARKER"

def fail(msg, code=1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)

if not BACKEND.is_dir():
    fail("backend/ not found. Run from the project root.")
if not LEGACY.exists():
    fail(f"{LEGACY.relative_to(ROOT)} not found.")
if not CFG_FILE.exists():
    fail(f"{CFG_FILE.relative_to(ROOT)} not found. PR-3 must be applied first.")
if not ENG_FILE.exists():
    fail(f"{ENG_FILE.relative_to(ROOT)} not found. PR-5 must be applied first.")

legacy_src = LEGACY.read_text()
if MARKER in legacy_src:
    print("Phase 9 PR-6 already applied — nothing to do.")
    sys.exit(0)

# ---------------------------------------------------------------------------
# New file: modules/auth/__init__.py
# ---------------------------------------------------------------------------
AUTH_SRC = '''\
"""
Aptiro backend — modules/auth/__init__.py (Phase 9 PR-6).

User model, password/token helpers, owner-scoping utilities,
Phase 4 auth router (/register /login /me), and _ensure_default_user.

Extracted from backend/app/legacy.py (Phase 4 auth block).

Dependencies:
  app.core.config   — AUTH_ENABLED, DEFAULT_UID, DEFAULT_USER_EMAIL, _PW_ROUNDS
  app.core.identity — _uid
  app.db.engine     — engine, get_session, _now
  sqlmodel, fastapi, pydantic, stdlib (hashlib, hmac, secrets)

Phase 8 additions kept in legacy.py (they reference the FastAPI app object):
  ExportToken, _auth8_router, _p8_token_expires/_set, _p8_ensure_columns,
  _p8_security_middleware, _p8_on_startup
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
    """UUID v4 hex — default_factory for the User primary key."""
    return _uuidmod.uuid4().hex


# ---------------------------------------------------------------------------
# User model
# ---------------------------------------------------------------------------

class User(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    email: str = Field(index=True)
    name: str = ""
    password_hash: str = ""
    token: str = Field(default="", index=True)
    is_default: bool = False
    created_at: datetime = Field(default_factory=_now)


# Phase 8: append token_expires_at to the User table at module-load time.
# The column is intentionally NOT on the Python class above; raw SQL is used
# for reads/writes (_p8_token_expires / _p8_set_token_expiry in legacy.py).
from sqlalchemy import Column as _p8_Column, DateTime as _p8_DateTime  # noqa: E402
if "token_expires_at" not in User.__table__.columns:
    User.__table__.append_column(
        _p8_Column("token_expires_at", _p8_DateTime(), nullable=True)
    )

# ---------------------------------------------------------------------------
# Auth helpers (Phase 4)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Owner-scoping utilities (Phase 4)
# ---------------------------------------------------------------------------

def _scoped(session, model):
    """Owner-filtered select for the current request\'s user. With AUTH
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


# ---------------------------------------------------------------------------
# Default user bootstrap (Phase 4)
# ---------------------------------------------------------------------------

def _ensure_default_user():
    """Ensure the built-in \'local\' user row exists. With AUTH off everything
    runs as this user, so prior behavior and data are unchanged."""
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


# ---------------------------------------------------------------------------
# Phase 4 auth router: /register  /login  /me
# Stdlib only (pbkdf2 + token); no new dependencies, no live service.
# ---------------------------------------------------------------------------
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
'''

# ---------------------------------------------------------------------------
# Five tight patterns for legacy.py
# ---------------------------------------------------------------------------

# P1: class User(SQLModel, table=True): ... fields
#     Replace with the combined import block (MARKER lives here).
#     Stops just before AuditEvent (the next model class).
PAT_USER = re.compile(
    r"class User\(SQLModel, table=True\):.*?(?=\n\nclass AuditEvent)",
    re.DOTALL,
)

# P2: Phase 8 User column addition block.
#     Stops after the append_column closing paren + newline.
PAT_P8_COL = re.compile(
    r"# The column is intentionally NOT on the User SQLModel Python class.*?"
    r"_p8_DateTime\(\), nullable=True\)\s*\)\s*\n",
    re.DOTALL,
)

# P3: Phase 4 auth + scoping helpers (hash, verify, bearer, scoped, get_owned).
#     Stops before the Parsing section separator.
PAT_HELPERS = re.compile(
    r"# --- Phase 4: auth \+ scoping helpers -+\n.*?"
    r"(?=\n# =====+\n# Parsing \+ extraction)",
    re.DOTALL,
)

# P4: _ensure_default_user function.
#     Stops just before init_db().
PAT_ENSURE = re.compile(
    r"def _ensure_default_user\(\):.*?(?=\n\ndef init_db\(\):)",
    re.DOTALL,
)

# P5: Phase 4 auth router section (comment + models + handlers).
#     Stops just before app.include_router(auth_router) — that line stays
#     in legacy so the router is registered on the FastAPI app object.
PAT_ROUTER = re.compile(
    r"# =====+\n# Phase 4 — auth: user accounts.*?"
    r"(?=\napp\.include_router\(auth_router\))",
    re.DOTALL,
)

IMPORT_BLOCK = (
    "# " + MARKER + "\n"
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

# ---------------------------------------------------------------------------
# Locate + validate all patterns
# ---------------------------------------------------------------------------
patterns = [
    ("User model",      PAT_USER,
     ["class User", "password_hash", "is_default"]),
    ("P8 col",          PAT_P8_COL,
     ["token_expires_at", "_p8_Column", "append_column"]),
    ("auth helpers",    PAT_HELPERS,
     ["_hash_pw", "_verify_pw", "_new_token", "_bearer", "_scoped", "_get_owned"]),
    ("_ensure_default", PAT_ENSURE,
     ["_ensure_default_user", "DEFAULT_UID", "Local User"]),
    ("auth router",     PAT_ROUTER,
     ["auth_router", "RegisterRequest", "register", "login", "whoami"]),
]

matches = {}
for name, pat, required in patterns:
    m = pat.search(legacy_src)
    if not m:
        fail(f"Pattern '{name}' not found in legacy.py.\n"
             f"The file layout may differ; inspect with:\n"
             f"  grep -n '{required[0]}' backend/app/legacy.py")
    missing = [r for r in required if r not in m.group(0)]
    if missing:
        fail(f"Pattern '{name}' matched but is missing {missing}.\n"
             f"Matched {len(m.group(0))} chars — may be too narrow.")
    if len(m.group(0)) > 8000:
        fail(f"Pattern '{name}' matched {len(m.group(0))} chars — suspiciously "
             f"large (PR-5 lesson: patterns must be tight).\n"
             f"First 200 chars of match:\n{m.group(0)[:200]}")
    matches[name] = m
    line = legacy_src[:m.start()].count("\n") + 1
    print(f"  ✓ {name:<18} line ~{line:>4}  ({len(m.group(0)):>5} chars)")

# Confirm ordering: User < P8_col, helpers, ensure < router
user_pos   = matches["User model"].start()
router_pos = matches["auth router"].start()
if user_pos > router_pos:
    fail("Unexpected: User model appears after auth router in legacy.py.")
print("  ✓ ordering confirmed (User before router)")

if args.dry_run:
    print(f"\n[dry-run] Would write {AUTH_FILE.relative_to(ROOT)} ({len(AUTH_SRC):,} chars)")
    print(f"[dry-run] Would apply 5 replacements to {LEGACY.relative_to(ROOT)}")
    print("[dry-run] No files written.")
    sys.exit(0)

# ---------------------------------------------------------------------------
# Step 1: Write modules/auth/__init__.py
# ---------------------------------------------------------------------------
AUTH_DIR.mkdir(parents=True, exist_ok=True)
print(f"\nWriting {AUTH_FILE.relative_to(ROOT)}...")
AUTH_FILE.write_text(AUTH_SRC)
print(f"  ✓ {len(AUTH_SRC):,} chars")

# ---------------------------------------------------------------------------
# Step 2: Patch legacy.py (back-to-front)
# ---------------------------------------------------------------------------
print(f"\nPatching {LEGACY.relative_to(ROOT)}...")

replacements = [
    (matches["auth router"].start(),     matches["auth router"].end(),
     "# auth_router imported from modules/auth (Phase 9 PR-6)\n",
     "auth router section"),
    (matches["_ensure_default"].start(), matches["_ensure_default"].end(),
     "# _ensure_default_user imported from modules/auth (Phase 9 PR-6)\n",
     "_ensure_default_user"),
    (matches["auth helpers"].start(),    matches["auth helpers"].end(),
     "# auth helpers imported from modules/auth (Phase 9 PR-6)\n",
     "auth helpers"),
    (matches["P8 col"].start(),          matches["P8 col"].end(),
     "# Phase 8 User column append moved to modules/auth (Phase 9 PR-6)\n",
     "P8 col"),
    (matches["User model"].start(),      matches["User model"].end(),
     IMPORT_BLOCK,
     "User model + import block"),
]
replacements.sort(key=lambda t: t[0], reverse=True)

patched = legacy_src
for start, end, repl, label in replacements:
    patched = patched[:start] + repl + patched[end:]
    print(f"  ✓ {label:<28} ({end-start:>5} → {len(repl):>4} chars)")

LEGACY.write_text(patched)

# ---------------------------------------------------------------------------
# Step 3: Byte-compile
# ---------------------------------------------------------------------------
print("\nByte-compiling...")
ok = True
for f in [AUTH_FILE, LEGACY]:
    try:
        py_compile.compile(str(f), doraise=True)
        print(f"  ✓ {f.relative_to(BACKEND)}")
    except py_compile.PyCompileError as exc:
        print(f"  ✗ {f.relative_to(BACKEND)}  — {exc}")
        ok = False
if not ok:
    fail("Compile failed.\nTo undo: git checkout backend/app/legacy.py", code=4)

# ---------------------------------------------------------------------------
# Step 4: Structural checks
# ---------------------------------------------------------------------------
print("\nPost-patch checks...")
final = LEGACY.read_text()

checks = [
    ("MARKER present",              MARKER in final),
    ("from app.modules.auth",       "from app.modules.auth import" in final),
    ("User in import",              "    User," in final),
    ("auth_router in import",       "    auth_router," in final),
    ("no class User(SQLModel",      "class User(SQLModel, table=True):" not in final),
    ("no def _hash_pw",             "def _hash_pw(" not in final),
    ("no def _ensure_default",      "def _ensure_default_user():" not in final),
    ("no auth_router = APIRouter",  "auth_router = APIRouter(" not in final),
    ("app.include_router stays",    "app.include_router(auth_router)" in final),
    ("AuditEvent still there",      "class AuditEvent(" in final),
    ("Source still there",          "class Source(" in final),
    ("_ensure_additive_columns ok", "def _ensure_additive_columns():" in final),
    ("init_db ok",                  "def init_db():" in final),
    ("_mw_session ok",              "def _mw_session():" in final),
    ("ExportToken still there",     "class ExportToken(" in final),
    ("_p8_security_middleware ok",  "_p8_security_middleware" in final),
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
Phase 9 PR-6 (modules/auth/) applied.

Files changed:
  backend/app/modules/auth/__init__.py  ← NEW
  backend/app/legacy.py                 ← PATCHED (5 blocks → imports)

Names moved: User, _hash_pw, _verify_pw, _new_token, _bearer,
  _scoped, _get_owned, auth_router, _ensure_default_user,
  RegisterRequest, LoginRequest, AuthOut, MeOut

Still in legacy.py (phase 8, reference FastAPI app object):
  ExportToken, _auth8_router, _p8_* helpers, security middleware

app.include_router(auth_router) kept in legacy — registers the router
on the FastAPI app instance (moves in final main.py cleanup, PR-16).

Run the full test suite:

  cd backend && . .venv/bin/activate && pytest -q

Expected: 218 passed, all green.

To undo:
  git checkout backend/app/legacy.py
  rm backend/app/modules/auth/__init__.py
═══════════════════════════════════════════════════════════════════════
""")
