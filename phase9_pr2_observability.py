#!/usr/bin/env python3
"""
Aptiro — Phase 9 PR-2: Extract core/observability.py

Moves the Phase 6 observability block out of backend/app/legacy.py
into backend/app/core/observability.py.

Names moved:
    _REQUEST_ID  — ContextVar[str], per-request correlation ID
    _rid()       — reads the current request ID from the ContextVar
    _log         — the "aptiro" logging.Logger singleton
    _logj()      — emits one structured JSON log line, never raises

After the move, legacy.py imports all four names back from the new
module so nothing changes for any caller:
    - `import app as A; A._logj(...)` still works (proxy → legacy → obs)
    - `A._log.addHandler(h)` still works
    - `A._REQUEST_ID.set(rid)` still works
    - The auth middleware's `_REQUEST_ID.set(rid)` still works (same object)
    - All 218 tests pass identically

core/observability.py is FULLY SELF-CONTAINED — no imports from any
other app.* module. This is the defining rule for every core/ module.

The module-level imports in legacy.py (_json, _logging, _time, _uuidmod)
that WERE part of the observability block are kept in legacy because they
are also used by the auth middleware (_time.perf_counter, _uuidmod.uuid4).

Run from the project root (directory containing backend/).
Idempotent — safe to re-run.

Usage:
    python3 phase9_pr2_observability.py
    python3 phase9_pr2_observability.py --dry-run
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
ROOT = pathlib.Path.cwd()
BACKEND = ROOT / "backend"
LEGACY = BACKEND / "app" / "legacy.py"
OBS_FILE = BACKEND / "app" / "core" / "observability.py"

MARKER = "APTIRO_PHASE9_PR2_OBS_MARKER"

# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
def fail(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)

if not BACKEND.is_dir():
    fail("backend/ not found. Run from the project root.")
if not LEGACY.exists():
    fail(f"{LEGACY.relative_to(ROOT)} not found.\n"
         "Phase 9 PR-1 must be applied first.")

# ---------------------------------------------------------------------------
# Idempotency check
# ---------------------------------------------------------------------------
legacy_src = LEGACY.read_text()
if MARKER in legacy_src:
    print("Phase 9 PR-2 already applied — nothing to do.")
    sys.exit(0)

# ---------------------------------------------------------------------------
# Content: the new core/observability.py
# ---------------------------------------------------------------------------
OBS_SRC = '''\
"""
Aptiro backend — core/observability.py (Phase 9 PR-2).

Structured JSON logging and per-request correlation ID.
Extracted from backend/app/legacy.py (the Phase 6 observability block).

Self-contained: no imports from other app.* modules.

Public names (all re-exported by legacy.py so `import app as A; A.X`
keeps working):
    _REQUEST_ID   ContextVar[str]  — set by auth middleware at request start
    _rid()        -> str           — read the current request\'s ID
    _log          logging.Logger   — the singleton "aptiro" logger
    _logj()       -> None          — emit one structured JSON line
"""
import json as _json
import logging as _logging
import os
import sys as _sys
from contextvars import ContextVar
from datetime import datetime, timezone


# Per-request correlation ID.
# The auth middleware in legacy.py calls _REQUEST_ID.set(rid) at the start
# of every request. Default "-" is used outside of request context (startup,
# shutdown, seed, unit tests that call _logj directly).
_REQUEST_ID: ContextVar[str] = ContextVar("aptiro_rid", default="-")


def _rid() -> str:
    """Return the active request\'s correlation ID (or "-" outside a request)."""
    return _REQUEST_ID.get()


# The single shared "aptiro" logger.
# Handler and level are configured once at module import time. APTIRO_LOG_LEVEL
# controls the level (default INFO).
_log = _logging.getLogger("aptiro")
if not _log.handlers:
    _h = _logging.StreamHandler(_sys.stdout)
    _h.setFormatter(_logging.Formatter("%(message)s"))
    _log.addHandler(_h)
    _log.setLevel(os.getenv("APTIRO_LOG_LEVEL", "INFO").upper())
    _log.propagate = False


def _logj(event: str, **fields) -> None:
    """Emit one structured JSON log line.

    Always includes: ts (ISO-8601 UTC), event, request_id.
    Additional keyword arguments are merged in.
    Never raises — log failures are silently swallowed to avoid masking
    application errors.
    """
    try:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "request_id": _rid(),
        }
        rec.update(fields)
        _log.info(_json.dumps(rec, default=str))
    except Exception:
        pass
'''

# ---------------------------------------------------------------------------
# Patch: what gets removed from legacy.py and what replaces it
# ---------------------------------------------------------------------------
# We remove the definitions (_REQUEST_ID, _rid, _log setup, _logj) but keep
# the import lines because _time and _uuidmod are still used in the auth
# middleware lower in legacy.py. The IMPORT lines become:
#   import time as _time        <- used: auth middleware _time.perf_counter()
#   import uuid as _uuidmod     <- used: auth middleware _uuidmod.uuid4().hex
# _json and _logging stay too (harmless, and safer than auditing every use).
LEGACY_REPLACEMENT = """\
# --- Phase 6: ops & observability (Phase 9 PR-2: moved to core/observability.py)
import json as _json         # kept — may be used elsewhere in legacy
import logging as _logging    # kept — may be used elsewhere in legacy
import time as _time          # KEPT — auth middleware: _time.perf_counter()
import uuid as _uuidmod       # KEPT — auth middleware: _uuidmod.uuid4().hex[:16]

# %(marker)s
# Definitions live in backend/app/core/observability.py.
# Imported here so the module proxy + `import app as A; A.X` contract holds.
from app.core.observability import (  # noqa: F401
    _REQUEST_ID,   # ContextVar[str] — per-request correlation ID
    _rid,          # () -> str — current request ID
    _log,          # logging.Logger — the "aptiro" logger singleton
    _logj,         # (event, **fields) -> None — structured JSON log line
)

""" % {"marker": MARKER}

# ---------------------------------------------------------------------------
# Find the observability block in legacy.py
# Pattern: from the Phase 6 comment banner through the _logj function body,
# stopping just before "class ConfigError" (the next top-level definition).
# ---------------------------------------------------------------------------
OBS_PATTERN = re.compile(
    r"# --- Phase 6: ops & observability\s*-+\s*"  # comment banner
    r"import json.*?"                               # imports start
    r"(?=\nclass ConfigError)",                     # stop before ConfigError
    re.DOTALL,
)

match = OBS_PATTERN.search(legacy_src)
if not match:
    fail(
        "Could not find the Phase 6 observability block in legacy.py.\n"
        "Expected a section starting with:\n"
        "  # --- Phase 6: ops & observability ---\n"
        "  import json as _json\n"
        "  ...\n"
        "  def _logj(event, **fields):\n"
        "      ...\n"
        "followed by 'class ConfigError'.\n\n"
        "The block may have already been moved, or the file layout changed."
    )

print(f"Found observability block in legacy.py "
      f"(lines ~{legacy_src[:match.start()].count(chr(10))+1}–"
      f"{legacy_src[:match.end()].count(chr(10))+1}).")

# Verify the block contains what we expect before touching anything.
block = match.group(0)
required_in_block = ["_REQUEST_ID", "_rid", "_logj", "_log.handlers"]
missing = [s for s in required_in_block if s not in block]
if missing:
    fail(f"Observability block found but is missing expected content: "
         f"{missing}. Aborting to avoid corrupting legacy.py.")
print(f"  ✓ block contains expected names: {required_in_block}")

if args.dry_run:
    print("\n[dry-run] Would write:")
    print(f"  {OBS_FILE.relative_to(ROOT)}  ({len(OBS_SRC)} chars)")
    print(f"  patch {LEGACY.relative_to(ROOT)}  "
          f"(replace {len(block)} chars with {len(LEGACY_REPLACEMENT)} chars)")
    print("\n[dry-run] No files written.")
    sys.exit(0)

# ---------------------------------------------------------------------------
# Step 1: Write core/observability.py
# ---------------------------------------------------------------------------
print(f"\nWriting {OBS_FILE.relative_to(ROOT)}...")
OBS_FILE.write_text(OBS_SRC)
print(f"  ✓ {len(OBS_SRC):,} chars written")

# ---------------------------------------------------------------------------
# Step 2: Patch legacy.py
# ---------------------------------------------------------------------------
print(f"\nPatching {LEGACY.relative_to(ROOT)}...")
new_legacy = legacy_src[:match.start()] + LEGACY_REPLACEMENT + legacy_src[match.end():]
LEGACY.write_text(new_legacy)
removed = len(block)
added = len(LEGACY_REPLACEMENT)
print(f"  ✓ replaced {removed:,} chars with {added:,} chars "
      f"({'−' if removed > added else '+'}{abs(removed-added):,} net)")

# ---------------------------------------------------------------------------
# Step 3: Verify compilation
# ---------------------------------------------------------------------------
print("\nByte-compiling...")
ok = True
for f in [OBS_FILE, LEGACY]:
    try:
        py_compile.compile(str(f), doraise=True)
        print(f"  ✓ {f.relative_to(BACKEND)}")
    except py_compile.PyCompileError as exc:
        print(f"  ✗ {f.relative_to(BACKEND)}  — {exc}")
        ok = False

if not ok:
    fail(
        "A file failed to compile. The most likely cause is a regex\n"
        "mismatch that cut too much or too little from legacy.py.\n\n"
        "To undo: git checkout backend/app/legacy.py",
        code=4,
    )

# ---------------------------------------------------------------------------
# Step 4: Verify the import line is actually in legacy.py
# ---------------------------------------------------------------------------
patched = LEGACY.read_text()
checks = [
    ("MARKER present",        MARKER in patched),
    ("from app.core.obs",     "from app.core.observability import" in patched),
    ("_REQUEST_ID imported",  "_REQUEST_ID" in patched),
    ("_logj imported",        "_logj" in patched),
    ("_log imported",         "_log" in patched),
    ("no _rid() def",         "def _rid():" not in patched),
    ("no _logj() def",        "def _logj(" not in patched),
    ("no _log setup",         "if not _log.handlers:" not in patched),
]
all_ok = True
for label, result in checks:
    status = "✓" if result else "✗"
    print(f"  {status} {label}")
    if not result:
        all_ok = False

if not all_ok:
    fail(
        "One or more post-patch checks failed (see ✗ lines above).\n"
        "Inspect backend/app/legacy.py around the observability section.\n"
        "To undo: git checkout backend/app/legacy.py",
        code=5,
    )

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
print(f"""
═══════════════════════════════════════════════════════════════════════
Phase 9 PR-2 (core/observability) applied.

Files changed:
  backend/app/core/observability.py   ← NEW — definitions live here now
  backend/app/legacy.py               ← PATCHED — imports from above

Names moved:
  _REQUEST_ID, _rid, _log, _logj

Nothing changes for callers. The proxy still works:
  import app as A; A._logj(...)   → legacy._logj → obs._logj  ✓
  A._log.addHandler(h)            → same logger object         ✓
  A._REQUEST_ID.set(rid)          → same ContextVar            ✓

Run the full test suite to confirm:

  cd backend
  . .venv/bin/activate
  pytest -q

Expected: same count as before (218), all green.

To undo:
  git checkout backend/app/legacy.py
  rm backend/app/core/observability.py
═══════════════════════════════════════════════════════════════════════
""")
