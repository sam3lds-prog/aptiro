#!/usr/bin/env python3
"""
Aptiro — Phase 9 PR-3: Extract core/config.py

Moves the configuration block out of backend/app/legacy.py into
backend/app/core/config.py.

Names moved:
    _DEFAULT_DATABASE_URL    str constant
    _resolve_database_url()  internal helper
    DATABASE_URL             resolved URL (sqlite or postgres)
    AI_PROVIDER              env var: APTIRO_AI_PROVIDER
    EMBEDDING_PROVIDER       env var: APTIRO_EMBEDDING_PROVIDER
    JOB_PROVIDER             env var: APTIRO_JOB_PROVIDER
    SEARCH_PROVIDER          env var: APTIRO_SEARCH_PROVIDER
    NOTIFICATION_PROVIDER    env var: APTIRO_NOTIFICATION_PROVIDER
    SEED_ON_STARTUP          bool flag
    AUTH_ENABLED             bool flag (auth on/off)
    DEFAULT_UID              "local"
    DEFAULT_USER_EMAIL       "local@aptiro.local"
    _PW_ROUNDS               int constant
    URL_FETCH_TIMEOUT        float (seconds)
    URL_FETCH_MAX_BYTES      int (bytes)
    ConfigError              RuntimeError subclass
    validate_config()        fail-fast config check

NOT moved (reserved for PR-4 — core/identity.py):
    _CURRENT_UID             ContextVar (runtime request state)
    _uid()                   reads _CURRENT_UID

core/config.py depends only on:
    os, sys, importlib.util  (stdlib)
    app.core.observability   (_logj, for the validate_config warning)

Two surgical replacements in legacy.py:
  1. Block from _DEFAULT_DATABASE_URL through _PW_ROUNDS
     (stops before "# Per-request current user id" comment)
  2. Block from class ConfigError through URL_FETCH_MAX_BYTES
     (stops before "# Hosts we will not fetch" comment)

Run from the project root (directory containing backend/).
Idempotent — safe to re-run.

Usage:
    python3 phase9_pr3_config.py
    python3 phase9_pr3_config.py --dry-run
"""
import argparse
import pathlib
import py_compile
import re
import sys

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
parser.add_argument("--dry-run", action="store_true",
                    help="Show what would change without writing anything.")
args = parser.parse_args()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT    = pathlib.Path.cwd()
BACKEND = ROOT / "backend"
LEGACY  = BACKEND / "app" / "legacy.py"
CFG     = BACKEND / "app" / "core" / "config.py"
OBS     = BACKEND / "app" / "core" / "observability.py"

MARKER = "APTIRO_PHASE9_PR3_CONFIG_MARKER"

# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
def fail(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)

if not BACKEND.is_dir():
    fail("backend/ not found. Run from the project root.")
if not LEGACY.exists():
    fail(f"{LEGACY.relative_to(ROOT)} not found. PR-1 must be applied first.")
if not OBS.exists():
    fail(f"{OBS.relative_to(ROOT)} not found. PR-2 must be applied first.")

legacy_src = LEGACY.read_text()

if MARKER in legacy_src:
    print("Phase 9 PR-3 already applied — nothing to do.")
    sys.exit(0)

# ---------------------------------------------------------------------------
# New file: core/config.py
# ---------------------------------------------------------------------------
# NOTE: the exact _resolve_database_url body, provider defaults, and
# validate_config body are reproduced here verbatim from the original
# app.py / legacy.py so behaviour is identical.
CFG_SRC = '''\
"""
Aptiro backend — core/config.py (Phase 9 PR-3).

All environment-variable-driven configuration, startup flags, URL-fetch
limits, ConfigError, and validate_config().

Extracted from backend/app/legacy.py.
Self-contained: depends only on stdlib + app.core.observability._logj.

Public names (all re-exported by legacy.py):
    _DEFAULT_DATABASE_URL, DATABASE_URL
    AI_PROVIDER, EMBEDDING_PROVIDER, JOB_PROVIDER
    SEARCH_PROVIDER, NOTIFICATION_PROVIDER, SEED_ON_STARTUP
    AUTH_ENABLED, DEFAULT_UID, DEFAULT_USER_EMAIL, _PW_ROUNDS
    URL_FETCH_TIMEOUT, URL_FETCH_MAX_BYTES
    ConfigError, validate_config
"""
import importlib.util as _ilu
import os
import sys as _sys

from app.core.observability import _logj  # used in validate_config warning

# ---------------------------------------------------------------------------
# Database URL
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Provider selection (all overridable via APTIRO_*_PROVIDER env vars)
# ---------------------------------------------------------------------------
AI_PROVIDER           = os.getenv("APTIRO_AI_PROVIDER",           "mock")
EMBEDDING_PROVIDER    = os.getenv("APTIRO_EMBEDDING_PROVIDER",    "mock")
JOB_PROVIDER          = os.getenv("APTIRO_JOB_PROVIDER",          "mock")
SEARCH_PROVIDER       = os.getenv("APTIRO_SEARCH_PROVIDER",       "mock")
NOTIFICATION_PROVIDER = os.getenv("APTIRO_NOTIFICATION_PROVIDER", "mock")
SEED_ON_STARTUP       = os.getenv("APTIRO_SEED_ON_STARTUP", "1") == "1"

# ---------------------------------------------------------------------------
# Auth constants (runtime identity state — _CURRENT_UID, _uid — lives in
# legacy.py and will move to core/identity.py in PR-4)
# ---------------------------------------------------------------------------
# AUTH defaults to OFF. With AUTH off every request runs as the single
# built-in "local" user so the entire prior test suite and existing
# single-user data behave EXACTLY as before.
AUTH_ENABLED      = os.getenv("APTIRO_AUTH", "off").lower() in (
    "on", "1", "true")
DEFAULT_UID       = "local"
DEFAULT_USER_EMAIL = "local@aptiro.local"
_PW_ROUNDS        = 120_000

# ---------------------------------------------------------------------------
# URL-fetch safety limits
# ---------------------------------------------------------------------------
# Server-side fetch of a user-supplied PUBLIC URL only — never a crawler.
URL_FETCH_TIMEOUT  = float(os.getenv("APTIRO_URL_FETCH_TIMEOUT",   "10"))
URL_FETCH_MAX_BYTES = int(
    os.getenv("APTIRO_URL_FETCH_MAX_BYTES", str(2 * 1024 * 1024)))

# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

class ConfigError(RuntimeError):
    pass


def validate_config():
    """Fail fast on genuinely invalid configuration with a clear message.

    Defaults are always valid, so normal startup and the test suite are
    unaffected; only explicitly bad env values trip this.
    """
    problems = []
    if not (DATABASE_URL or "").strip():
        problems.append("APTIRO_DATABASE_URL is empty")
    a = os.getenv("APTIRO_AUTH", "off").lower()
    if a not in ("on", "off", "1", "0", "true", "false"):
        problems.append("APTIRO_AUTH must be on/off (got %r)" % a)
    for name, raw in (
            ("APTIRO_URL_FETCH_TIMEOUT",
             os.getenv("APTIRO_URL_FETCH_TIMEOUT")),
            ("APTIRO_URL_FETCH_MAX_BYTES",
             os.getenv("APTIRO_URL_FETCH_MAX_BYTES")),
            ("APTIRO_AI_MAX_TOKENS", os.getenv("APTIRO_AI_MAX_TOKENS")),
            ("APTIRO_AI_TIMEOUT",    os.getenv("APTIRO_AI_TIMEOUT"))):
        if raw is None:
            continue
        try:
            if float(raw) <= 0:
                problems.append(
                    "%s must be > 0 (got %r)" % (name, raw))
        except (TypeError, ValueError):
            problems.append(
                "%s must be numeric (got %r)" % (name, raw))
    if (os.getenv("APTIRO_AI_PROVIDER", "mock").lower() == "anthropic"
            and not os.getenv("ANTHROPIC_API_KEY")):
        # Not fatal: provider falls back to mock, but make it loud.
        _logj("config.warning",
              message="APTIRO_AI_PROVIDER=anthropic but ANTHROPIC_API_"
                      "KEY is unset; falling back to the mock provider")
    if problems:
        raise ConfigError(
            "Invalid configuration: " + "; ".join(problems))
    return True
'''

# ---------------------------------------------------------------------------
# Patch patterns for legacy.py
# ---------------------------------------------------------------------------
# Pattern 1: from _DEFAULT_DATABASE_URL through _PW_ROUNDS = 120_000
# Stops just before the "# Per-request current user id" comment that
# introduces _CURRENT_UID (which stays in legacy until PR-4).
PAT1 = re.compile(
    r"_DEFAULT_DATABASE_URL = [^\n]+\n"   # opening constant
    r".*?"                                 # everything between (DOTALL)
    r"_PW_ROUNDS\s*=\s*120_000[^\n]*\n",  # last line of this block
    re.DOTALL,
)

# Pattern 2: from class ConfigError through URL_FETCH_MAX_BYTES.
# Stops just before "# Hosts we will not fetch" (the blocked-hosts list
# that stays in legacy as it's part of the URL fetch logic, not config).
PAT2 = re.compile(
    r"class ConfigError\(RuntimeError\):.*?"   # ConfigError + validate_config
    r"(?=\n# Hosts we will not fetch)",         # stop before blocked-hosts
    re.DOTALL,
)

REPLACEMENT1 = (
    "# APTIRO_PHASE9_PR3_CONFIG_MARKER\n"
    "# Config vars, auth constants: extracted to core/config.py (Phase 9 PR-3)\n"
    "from app.core.config import (  # noqa: F401\n"
    "    _DEFAULT_DATABASE_URL, DATABASE_URL,\n"
    "    AI_PROVIDER, EMBEDDING_PROVIDER, JOB_PROVIDER,\n"
    "    SEARCH_PROVIDER, NOTIFICATION_PROVIDER, SEED_ON_STARTUP,\n"
    "    AUTH_ENABLED, DEFAULT_UID, DEFAULT_USER_EMAIL, _PW_ROUNDS,\n"
    ")\n"
)

REPLACEMENT2 = (
    "# ConfigError, validate_config, URL limits: extracted to core/config.py (Phase 9 PR-3)\n"
    "from app.core.config import (  # noqa: F401\n"
    "    ConfigError, validate_config,\n"
    "    URL_FETCH_TIMEOUT, URL_FETCH_MAX_BYTES,\n"
    ")\n"
)

# ---------------------------------------------------------------------------
# Verify patterns match before touching anything
# ---------------------------------------------------------------------------
m1 = PAT1.search(legacy_src)
m2 = PAT2.search(legacy_src)

if not m1:
    fail(
        "Could not find the config block in legacy.py.\n"
        "Expected: _DEFAULT_DATABASE_URL = ... through _PW_ROUNDS = 120_000\n"
        "This block may have already been moved or the layout changed."
    )
if not m2:
    fail(
        "Could not find ConfigError/validate_config block in legacy.py.\n"
        "Expected: class ConfigError(RuntimeError): ... URL_FETCH_MAX_BYTES\n"
        "followed by '# Hosts we will not fetch'."
    )

print(f"Pattern 1 found: lines "
      f"~{legacy_src[:m1.start()].count(chr(10))+1}–"
      f"{legacy_src[:m1.end()].count(chr(10))+1} "
      f"({len(m1.group(0)):,} chars)")
print(f"Pattern 2 found: lines "
      f"~{legacy_src[:m2.start()].count(chr(10))+1}–"
      f"{legacy_src[:m2.end()].count(chr(10))+1} "
      f"({len(m2.group(0)):,} chars)")

# Spot-check content of each match
for label, m, required in [
    ("block 1", m1, ["_DEFAULT_DATABASE_URL", "DATABASE_URL", "AUTH_ENABLED",
                     "DEFAULT_UID", "SEED_ON_STARTUP", "_PW_ROUNDS"]),
    ("block 2", m2, ["ConfigError", "validate_config", "URL_FETCH_TIMEOUT",
                     "URL_FETCH_MAX_BYTES"]),
]:
    missing = [s for s in required if s not in m.group(0)]
    if missing:
        fail(f"{label} matched but is missing expected names: {missing}.\n"
             f"Aborting to avoid corrupting legacy.py.")
    print(f"  ✓ {label} contains: {required}")

# Verify the two matches don't overlap
if m1.end() > m2.start():
    fail("Patterns overlap — block 1 extends past block 2 start. "
         "Check legacy.py structure.")

if args.dry_run:
    print(f"\n[dry-run] Would write {CFG.relative_to(ROOT)} "
          f"({len(CFG_SRC):,} chars)")
    print(f"[dry-run] Would apply 2 replacements to "
          f"{LEGACY.relative_to(ROOT)}")
    print("[dry-run] No files written.")
    sys.exit(0)

# ---------------------------------------------------------------------------
# Step 1: Write core/config.py
# ---------------------------------------------------------------------------
print(f"\nWriting {CFG.relative_to(ROOT)}...")
CFG.write_text(CFG_SRC)
print(f"  ✓ {len(CFG_SRC):,} chars")

# ---------------------------------------------------------------------------
# Step 2: Patch legacy.py — two replacements (back-to-front to keep offsets)
# ---------------------------------------------------------------------------
print(f"\nPatching {LEGACY.relative_to(ROOT)}...")

# Apply from end to start so the first match's offset stays valid.
patched = (
    legacy_src[:m1.start()]
    + REPLACEMENT1
    + legacy_src[m1.end():m2.start()]
    + REPLACEMENT2
    + legacy_src[m2.end():]
)
LEGACY.write_text(patched)
print(f"  ✓ replaced block 1 ({len(m1.group(0)):,} → "
      f"{len(REPLACEMENT1):,} chars)")
print(f"  ✓ replaced block 2 ({len(m2.group(0)):,} → "
      f"{len(REPLACEMENT2):,} chars)")

# ---------------------------------------------------------------------------
# Step 3: Byte-compile both files
# ---------------------------------------------------------------------------
print("\nByte-compiling...")
ok = True
for f in [CFG, LEGACY]:
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
    ("from app.core.config import", "from app.core.config import" in final),
    ("no _DEFAULT_DATABASE_URL def", "_DEFAULT_DATABASE_URL = " not in final),
    ("no DATABASE_URL = assignment", "DATABASE_URL = _resolve_database_url" not in final),
    ("no AUTH_ENABLED = assignment", "AUTH_ENABLED = os.getenv" not in final),
    ("no class ConfigError def",    "class ConfigError(RuntimeError):" not in final),
    ("no def validate_config",      "def validate_config():" not in final),
    ("no URL_FETCH_TIMEOUT = ass.",  "URL_FETCH_TIMEOUT = float" not in final),
    ("_CURRENT_UID still present",   "_CURRENT_UID = ContextVar" in final),
    ("_uid() still present",         "def _uid():" in final),
    ("Hosts comment still present",  "# Hosts we will not fetch" in final),
]

all_ok = True
for label, result in checks:
    print(f"  {'✓' if result else '✗'} {label}")
    if not result:
        all_ok = False

if not all_ok:
    fail("One or more structural checks failed.\n"
         "To undo: git checkout backend/app/legacy.py", code=5)

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
print(f"""
═══════════════════════════════════════════════════════════════════════
Phase 9 PR-3 (core/config.py) applied.

Files changed:
  backend/app/core/config.py     ← NEW — all config definitions
  backend/app/legacy.py          ← PATCHED — 2 blocks → imports

Names moved ({17} total):
  _DEFAULT_DATABASE_URL, DATABASE_URL, AI_PROVIDER, EMBEDDING_PROVIDER,
  JOB_PROVIDER, SEARCH_PROVIDER, NOTIFICATION_PROVIDER, SEED_ON_STARTUP,
  AUTH_ENABLED, DEFAULT_UID, DEFAULT_USER_EMAIL, _PW_ROUNDS,
  URL_FETCH_TIMEOUT, URL_FETCH_MAX_BYTES, ConfigError, validate_config,
  _resolve_database_url (internal helper)

Still in legacy.py (reserved for PR-4):
  _CURRENT_UID, _uid()

Run the full test suite to confirm:

  cd backend
  . .venv/bin/activate
  pytest -q

Expected: 218 passed, all green.

To undo:
  git checkout backend/app/legacy.py
  rm backend/app/core/config.py
═══════════════════════════════════════════════════════════════════════
""")
