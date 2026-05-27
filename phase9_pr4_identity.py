#!/usr/bin/env python3
"""
Aptiro — Phase 9 PR-4: Extract core/identity.py

Moves the per-request identity block out of backend/app/legacy.py into
backend/app/core/identity.py.

Names moved:
    _CURRENT_UID   ContextVar[str] — holds the current request's user ID
    _uid()         -> str          — reads _CURRENT_UID (or DEFAULT_UID)

Depends only on:
    contextvars    (stdlib)
    app.core.config.DEFAULT_UID   (already extracted in PR-3)

This completes the core/ directory:
    core/observability.py   (PR-2) — _log, _logj, _REQUEST_ID, _rid
    core/config.py          (PR-3) — env vars, auth constants, validate_config
    core/identity.py        (PR-4) — _CURRENT_UID, _uid  ← this PR

After PR-4, legacy.py contains only one import line where this block was.
The auth middleware in legacy.py still uses _CURRENT_UID.set(uid) and
_CURRENT_UID.reset(utok) — these work because the same ContextVar object
is imported back into legacy's namespace.

Run from the project root (directory containing backend/).
Idempotent — safe to re-run.

Usage:
    python3 phase9_pr4_identity.py
    python3 phase9_pr4_identity.py --dry-run
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
ROOT     = pathlib.Path.cwd()
BACKEND  = ROOT / "backend"
LEGACY   = BACKEND / "app" / "legacy.py"
IDN_FILE = BACKEND / "app" / "core" / "identity.py"
CFG_FILE = BACKEND / "app" / "core" / "config.py"

MARKER = "APTIRO_PHASE9_PR4_IDENTITY_MARKER"

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
if not CFG_FILE.exists():
    fail(f"{CFG_FILE.relative_to(ROOT)} not found. PR-3 must be applied first.")

legacy_src = LEGACY.read_text()

if MARKER in legacy_src:
    print("Phase 9 PR-4 already applied — nothing to do.")
    sys.exit(0)

# ---------------------------------------------------------------------------
# New file: core/identity.py
# ---------------------------------------------------------------------------
IDN_SRC = '''\
"""
Aptiro backend — core/identity.py (Phase 9 PR-4).

Per-request identity: the ContextVar holding the current user ID and
the accessor function.

Extracted from backend/app/legacy.py.

Self-contained: depends only on stdlib + app.core.config.DEFAULT_UID.

Public names (re-exported by legacy.py so `import app as A; A.X` works):
    _CURRENT_UID   ContextVar[str]  — set by auth middleware at request start
    _uid()         -> str           — read the current request\'s user ID

The auth middleware in legacy.py calls:
    utok = _CURRENT_UID.set(uid)     # set for this request
    ...
    _CURRENT_UID.reset(utok)         # restore previous value

These use the same ContextVar object exported here, so the context
isolation is preserved exactly as before.
"""
from contextvars import ContextVar

from app.core.config import DEFAULT_UID

# Per-request current user id.
# Defaults to DEFAULT_UID ("local") so code paths with no auth context
# (tests, single-user mode) work identically to pre-Phase-4 behaviour.
_CURRENT_UID: ContextVar[str] = ContextVar("aptiro_uid", default=DEFAULT_UID)


def _uid() -> str:
    """Return the current request\'s user ID.

    Returns DEFAULT_UID ("local") when called outside a request context,
    e.g. in tests, startup code, or single-user mode.
    """
    return _CURRENT_UID.get()
'''

# ---------------------------------------------------------------------------
# Patch pattern for legacy.py
# ---------------------------------------------------------------------------
# Match the identity block: from the "Per-request current user id" comment
# through the closing line of _uid(). Stops before the blank line that
# follows.
#
# After PR-3, the block in legacy.py looks like:
#
#   # Per-request current user id. Defaults to the local user so code paths
#   # with no auth context (tests, single-user mode) just work.
#   _CURRENT_UID = ContextVar("aptiro_uid", default=DEFAULT_UID)
#
#
#   def _uid():
#       return _CURRENT_UID.get()
#
PAT = re.compile(
    r"# Per-request current user id\..*?"         # comment
    r"def _uid\(\):\s*\n"                          # function def
    r"[ \t]+return _CURRENT_UID\.get\(\)\s*\n",   # function body
    re.DOTALL,
)

REPLACEMENT = (
    "# APTIRO_PHASE9_PR4_IDENTITY_MARKER\n"
    "# Identity extracted to core/identity.py (Phase 9 PR-4)\n"
    "from app.core.identity import _CURRENT_UID, _uid  # noqa: F401\n"
)

# ---------------------------------------------------------------------------
# Verify pattern matches before touching anything
# ---------------------------------------------------------------------------
m = PAT.search(legacy_src)
if not m:
    fail(
        "Could not find the identity block in legacy.py.\n"
        "Expected:\n"
        "  # Per-request current user id...\n"
        "  _CURRENT_UID = ContextVar(...)\n"
        "  def _uid():\n"
        "      return _CURRENT_UID.get()\n\n"
        "It may already have been moved, or the layout changed."
    )

block = m.group(0)
for name in ("_CURRENT_UID", "_uid", "ContextVar"):
    if name not in block:
        fail(f"Matched block is missing '{name}'. Aborting.")

start_line = legacy_src[:m.start()].count("\n") + 1
end_line   = legacy_src[:m.end()].count("\n") + 1
print(f"Found identity block: lines ~{start_line}–{end_line} "
      f"({len(block):,} chars).")
print(f"  ✓ contains: _CURRENT_UID, _uid, ContextVar")

if args.dry_run:
    print(f"\n[dry-run] Would write {IDN_FILE.relative_to(ROOT)} "
          f"({len(IDN_SRC):,} chars)")
    print(f"[dry-run] Would replace {len(block):,} chars in legacy.py "
          f"with {len(REPLACEMENT):,} chars")
    print("[dry-run] No files written.")
    sys.exit(0)

# ---------------------------------------------------------------------------
# Step 1: Write core/identity.py
# ---------------------------------------------------------------------------
print(f"\nWriting {IDN_FILE.relative_to(ROOT)}...")
IDN_FILE.write_text(IDN_SRC)
print(f"  ✓ {len(IDN_SRC):,} chars")

# ---------------------------------------------------------------------------
# Step 2: Patch legacy.py
# ---------------------------------------------------------------------------
print(f"\nPatching {LEGACY.relative_to(ROOT)}...")
patched = legacy_src[:m.start()] + REPLACEMENT + legacy_src[m.end():]
LEGACY.write_text(patched)
print(f"  ✓ replaced {len(block):,} chars → {len(REPLACEMENT):,} chars")

# ---------------------------------------------------------------------------
# Step 3: Byte-compile
# ---------------------------------------------------------------------------
print("\nByte-compiling...")
ok = True
for f in [IDN_FILE, LEGACY]:
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
    ("from app.core.identity",      "from app.core.identity import" in final),
    ("no _CURRENT_UID = ContextVar","_CURRENT_UID = ContextVar" not in final),
    ("no def _uid():",              "def _uid():" not in final),
    ("auth middleware intact",      "_CURRENT_UID.set(uid)" in final),
    ("ContextVar still imported",   "ContextVar" in final),
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
Phase 9 PR-4 (core/identity.py) applied.

Files changed:
  backend/app/core/identity.py   ← NEW — _CURRENT_UID and _uid live here
  backend/app/legacy.py          ← PATCHED — 1 block → 1 import line

Names moved:
  _CURRENT_UID   ContextVar[str]
  _uid()         -> str

core/ directory is now complete:
  core/observability.py  (PR-2)
  core/config.py         (PR-3)
  core/identity.py       (PR-4)  ← just landed

Run the full test suite to confirm:

  cd backend
  . .venv/bin/activate
  pytest -q

Expected: 218 passed, all green.

To undo:
  git checkout backend/app/legacy.py
  rm backend/app/core/identity.py
═══════════════════════════════════════════════════════════════════════
""")
