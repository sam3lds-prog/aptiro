#!/usr/bin/env python3
"""
Aptiro — Phase 9 PR-5: Extract db/engine.py

Moves the database engine block out of backend/app/legacy.py into
backend/app/db/engine.py.

Names moved:
    _now()           -> datetime  UTC timestamp factory (model default_factory)
    _IS_SQLITE       bool         True when DATABASE_URL starts with "sqlite"
    _connect_args    dict         {"check_same_thread": False} for SQLite only
    engine           Engine       SQLAlchemy engine instance
    get_session()    Generator    FastAPI dependency — yields a Session

NOT moved (must stay in legacy.py):
    _ensure_additive_columns()  — test patches A.engine THEN calls this;
                                   if moved to db/engine.py it reads
                                   db.engine.engine (original) instead of
                                   the patched value. Stays until that
                                   test is rewritten (PR-6 or later).
    _backfill_owner_ids()       — uses engine; kept alongside above.
    _ensure_default_user()      — uses User SQLModel; moves in PR-6.
    init_db()                   — calls all three; moves in PR-6.
    _mw_session()               — references app (FastAPI instance); moves
                                   last, when app is extracted to main.py.

db/engine.py depends only on:
    app.core.config  (DATABASE_URL)
    sqlmodel         (create_engine, Session)
    stdlib           (datetime, sys)

Three surgical replacements in legacy.py:
  1. Remove def _now(): return datetime.now(timezone.utc)   [if standalone]
  2. Replace _IS_SQLITE / _connect_args / engine block → import (MARKER here)
  3. Remove def get_session(): ... yield s

Run from the project root (directory containing backend/).
Idempotent — safe to re-run.

Usage:
    python3 phase9_pr5_db_engine.py
    python3 phase9_pr5_db_engine.py --dry-run
"""
import argparse
import pathlib
import py_compile
import re
import sys

parser = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
parser.add_argument("--dry-run", action="store_true",
                    help="Show what would change without writing anything.")
args = parser.parse_args()

ROOT     = pathlib.Path.cwd()
BACKEND  = ROOT / "backend"
LEGACY   = BACKEND / "app" / "legacy.py"
DB_DIR   = BACKEND / "app" / "db"
ENG_FILE = DB_DIR / "engine.py"
CFG_FILE = BACKEND / "app" / "core" / "config.py"
MARKER   = "APTIRO_PHASE9_PR5_DB_MARKER"

def fail(msg, code=1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)

if not BACKEND.is_dir():
    fail("backend/ not found. Run from the project root.")
if not LEGACY.exists():
    fail(f"{LEGACY.relative_to(ROOT)} not found. PR-1 must be applied first.")
if not CFG_FILE.exists():
    fail(f"{CFG_FILE.relative_to(ROOT)} not found. PR-3 must be applied first.")

legacy_src = LEGACY.read_text()

if MARKER in legacy_src:
    print("Phase 9 PR-5 already applied — nothing to do.")
    sys.exit(0)

# ---------------------------------------------------------------------------
# New file: db/engine.py
# ---------------------------------------------------------------------------
ENG_SRC = '''\
"""
Aptiro backend — db/engine.py (Phase 9 PR-5).

SQLAlchemy/SQLModel engine, session dependency, and the UTC timestamp
factory used throughout the application as a model default_factory.

Extracted from backend/app/legacy.py.

Depends only on:
  app.core.config  — DATABASE_URL
  sqlmodel         — create_engine, Session
  stdlib           — datetime, sys

Public names (re-exported by legacy.py):
    _now()      → datetime  UTC timestamp (model Field default_factory)
    _IS_SQLITE  bool        True when database is SQLite
    engine      Engine      the live SQLAlchemy engine
    get_session() Generator FastAPI dependency — yields a Session

NOT here (still in legacy.py — see script docstring for why):
    _ensure_additive_columns, _backfill_owner_ids, _ensure_default_user,
    init_db, _mw_session
"""
import sys as _sys
from datetime import datetime, timezone

from sqlmodel import Session, create_engine

from app.core.config import DATABASE_URL  # noqa: F401


def _now() -> datetime:
    """UTC timestamp — used as default_factory in every SQLModel Field
    that records a creation or update time."""
    return datetime.now(timezone.utc)


_IS_SQLITE: bool = DATABASE_URL.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _IS_SQLITE else {}

engine = create_engine(DATABASE_URL, echo=False, connect_args=_connect_args)


def get_session():
    """FastAPI dependency — yields an open SQLModel Session.

    Tests override this via app.dependency_overrides so they get an
    in-memory SQLite session instead of the real database.
    """
    with Session(engine) as s:
        yield s
'''

# ---------------------------------------------------------------------------
# Patch patterns
# ---------------------------------------------------------------------------

# Pattern 1 (optional): standalone _now() definition before the engine block
PAT_NOW = re.compile(
    r"def _now\(\):\s*\n"
    r"[ \t]+return datetime\.now\(timezone\.utc\)[ \t]*\n",
    re.DOTALL,
)
REPL_NOW = "# _now imported from app.db.engine (Phase 9 PR-5)\n"

# Pattern 2 (required): _IS_SQLITE + _connect_args + engine = create_engine(...)
PAT_ENGINE = re.compile(
    r"_IS_SQLITE = DATABASE_URL\.startswith.*?"
    r"engine = create_engine\(DATABASE_URL[^)]+\)[ \t]*\n",
    re.DOTALL,
)
REPL_ENGINE = (
    "# " + MARKER + "\n"
    "# _now, _IS_SQLITE, engine, get_session extracted to db/engine.py (Phase 9 PR-5)\n"
    "from app.db.engine import (  # noqa: F401\n"
    "    _now, _IS_SQLITE, engine, get_session,\n"
    ")\n"
)

# Pattern 3 (required): get_session() definition
PAT_SESSION = re.compile(
    r"def get_session\(\):\s*\n"
    r"[ \t]+with Session\(engine\) as s:\s*\n"
    r"[ \t]+yield s[ \t]*\n",
    re.DOTALL,
)
REPL_SESSION = "# get_session imported from app.db.engine (Phase 9 PR-5)\n"

# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------
m_engine  = PAT_ENGINE.search(legacy_src)
m_session = PAT_SESSION.search(legacy_src)
m_now     = PAT_NOW.search(legacy_src)

if not m_engine:
    fail(
        "Could not find the engine block in legacy.py.\n"
        "Expected: _IS_SQLITE = DATABASE_URL.startswith(...)\n"
        "          ...\n"
        "          engine = create_engine(DATABASE_URL...)"
    )
if not m_session:
    fail(
        "Could not find def get_session(): in legacy.py.\n"
        "Expected:\n"
        "  def get_session():\n"
        "      with Session(engine) as s:\n"
        "          yield s"
    )
for label, m, names in [
    ("engine block", m_engine,  ["_IS_SQLITE", "_connect_args", "create_engine"]),
    ("get_session",  m_session,  ["Session",    "engine",        "yield"]),
]:
    missing = [n for n in names if n not in m.group(0)]
    if missing:
        fail(f"{label}: matched but missing expected names {missing}.")

if m_engine.start() > m_session.start():
    fail("Unexpected order: engine block appears after get_session.")

print(f"engine block : lines ~{legacy_src[:m_engine.start()].count(chr(10))+1}"
      f"–{legacy_src[:m_engine.end()].count(chr(10))+1} ({len(m_engine.group(0)):,} chars)")
print(f"get_session  : line  ~{legacy_src[:m_session.start()].count(chr(10))+1}"
      f" ({len(m_session.group(0)):,} chars)")
if m_now:
    print(f"_now()       : line  ~{legacy_src[:m_now.start()].count(chr(10))+1}"
          f" — will be removed separately")
else:
    print("_now()       : not found as standalone (may be inside engine block — OK)")

if args.dry_run:
    print(f"\n[dry-run] Would write {ENG_FILE.relative_to(ROOT)} ({len(ENG_SRC):,} chars)")
    print(f"[dry-run] Would apply 2-3 replacements to {LEGACY.relative_to(ROOT)}")
    print("[dry-run] No files written.")
    sys.exit(0)

# ---------------------------------------------------------------------------
# Step 1: Write db/engine.py
# ---------------------------------------------------------------------------
DB_DIR.mkdir(parents=True, exist_ok=True)
print(f"\nWriting {ENG_FILE.relative_to(ROOT)}...")
ENG_FILE.write_text(ENG_SRC)
print(f"  ✓ {len(ENG_SRC):,} chars")

# ---------------------------------------------------------------------------
# Step 2: Patch legacy.py (back-to-front to preserve offsets)
# ---------------------------------------------------------------------------
print(f"\nPatching {LEGACY.relative_to(ROOT)}...")

replacements = [
    (m_session.start(), m_session.end(), REPL_SESSION, "get_session def"),
    (m_engine.start(),  m_engine.end(),  REPL_ENGINE,  "engine block"),
]
if m_now and m_now.start() < m_engine.start():
    replacements.append((m_now.start(), m_now.end(), REPL_NOW, "_now() def"))

replacements.sort(key=lambda t: t[0], reverse=True)  # back-to-front

patched = legacy_src
for start, end, repl, label in replacements:
    patched = patched[:start] + repl + patched[end:]
    print(f"  ✓ replaced {label} ({end-start:,} → {len(repl):,} chars)")

LEGACY.write_text(patched)

# ---------------------------------------------------------------------------
# Step 3: Byte-compile
# ---------------------------------------------------------------------------
print("\nByte-compiling...")
ok = True
for f in [ENG_FILE, LEGACY]:
    try:
        py_compile.compile(str(f), doraise=True)
        print(f"  ✓ {f.relative_to(BACKEND)}")
    except py_compile.PyCompileError as exc:
        print(f"  ✗ {f.relative_to(BACKEND)}  —  {exc}")
        ok = False

if not ok:
    fail("A file failed to compile.\n"
         "To undo: git checkout backend/app/legacy.py", code=4)

# ---------------------------------------------------------------------------
# Step 4: Structural checks
# ---------------------------------------------------------------------------
print("\nPost-patch checks...")
final = LEGACY.read_text()

checks = [
    ("MARKER present",              MARKER in final),
    ("from app.db.engine import",   "from app.db.engine import" in final),
    ("_now in import",              "_now," in final),
    ("engine in import",            "engine," in final),
    ("get_session in import",       "get_session," in final),
    ("no _IS_SQLITE = assignment",  "_IS_SQLITE = DATABASE_URL" not in final),
    ("no engine = create_engine",   "engine = create_engine(" not in final),
    ("no def get_session",          "def get_session():" not in final),
    ("_ensure_additive_columns ok", "def _ensure_additive_columns():" in final),
    ("_backfill_owner_ids ok",      "def _backfill_owner_ids():" in final),
    ("_ensure_default_user ok",     "def _ensure_default_user():" in final),
    ("init_db ok",                  "def init_db():" in final),
    ("_mw_session ok",              "def _mw_session():" in final),
]

all_ok = True
for label, result in checks:
    print(f"  {'✓' if result else '✗'} {label}")
    if not result:
        all_ok = False

if not all_ok:
    fail("One or more structural checks failed.\n"
         "To undo: git checkout backend/app/legacy.py", code=5)

print(f"""
═══════════════════════════════════════════════════════════════════════
Phase 9 PR-5 (db/engine.py) applied.

Files changed:
  backend/app/db/engine.py   ← NEW — _now, engine, get_session live here
  backend/app/legacy.py      ← PATCHED — 2-3 blocks → imports

Names moved: _now, _IS_SQLITE, _connect_args, engine, get_session

Intentionally NOT moved (engine-dependent tests require them in legacy):
  _ensure_additive_columns, _backfill_owner_ids, _ensure_default_user,
  init_db, _mw_session

Run the full test suite:

  cd backend
  . .venv/bin/activate
  pytest -q

Expected: 218 passed, all green.

To undo:
  git checkout backend/app/legacy.py
  rm backend/app/db/engine.py
═══════════════════════════════════════════════════════════════════════
""")
