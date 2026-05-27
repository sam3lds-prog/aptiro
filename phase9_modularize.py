#!/usr/bin/env python3
"""
Aptiro — Phase 9 PR-1: Backend modularization scaffold.

Per ROADMAP.md Phase 9: "one PR = one moved module, tests green before
and after, zero behaviour change." This script is PR-1: it creates the
target package scaffold at `backend/app/{core,db,modules/*}/` and moves
the existing `backend/app.py` to `backend/app/legacy.py`, with the new
package's `__init__.py` dynamically re-exporting every top-level name
from `legacy` so the existing test contract is preserved exactly:

    import app as A
    A.app               # FastAPI instance
    A.engine            # SQLAlchemy engine
    A.Session           # sqlmodel.Session
    A.Source            # SQLModel
    A.JobPosting        # SQLModel
    A.parse_document(...)
    A._uid()  A._logj(...)  A._mw_session()  ...   (private helpers)

After PR-1:
  - All actual code still lives in `backend/app/legacy.py` (verbatim
    copy of what was `backend/app.py`).
  - Test suite passes identically — same count, same names, zero modified.
  - `backend/app/` has scaffold __init__.py files for every target module:
      core/  db/  modules/{sources, profile_truth, jobs, strategies,
                            scoring, packages, research, applications,
                            notifications, auth}/

PR-2..PR-N (separate chats / separate PRs) move chunks out of legacy.py
into the proper module files one at a time. See PHASE9_NEXT_STEPS.md.

Run from the project root (the directory containing `backend/`).
Idempotent — safe to re-run. Supports --dry-run and --revert.

Usage:
    python3 phase9_modularize.py
    python3 phase9_modularize.py --dry-run   # show changes, write nothing
    python3 phase9_modularize.py --revert    # undo PR-1 (restore app.py)
"""
import argparse
import pathlib
import py_compile
import shutil
import sys


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
parser.add_argument("--dry-run", action="store_true",
                    help="Print planned changes without writing anything.")
parser.add_argument("--revert", action="store_true",
                    help="Undo PR-1: restore backend/app.py from legacy.py "
                         "and remove the empty scaffold.")
args = parser.parse_args()


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = pathlib.Path.cwd()
BACKEND = ROOT / "backend"
APP_FILE = BACKEND / "app.py"
APP_PKG = BACKEND / "app"
LEGACY_FILE = APP_PKG / "legacy.py"
INIT_FILE = APP_PKG / "__init__.py"

# Target scaffold — mirrors ROADMAP.md Phase 9 target structure.
SCAFFOLD_DIRS = [
    APP_PKG / "core",
    APP_PKG / "db",
    APP_PKG / "modules",
    APP_PKG / "modules" / "sources",
    APP_PKG / "modules" / "profile_truth",
    APP_PKG / "modules" / "jobs",
    APP_PKG / "modules" / "strategies",
    APP_PKG / "modules" / "scoring",
    APP_PKG / "modules" / "packages",
    APP_PKG / "modules" / "research",
    APP_PKG / "modules" / "applications",
    APP_PKG / "modules" / "notifications",
    APP_PKG / "modules" / "auth",
]


# ---------------------------------------------------------------------------
# Content templates
# ---------------------------------------------------------------------------
INIT_PY_SRC = '''\
"""
Aptiro backend — modular package (`backend/app/`).

Phase 9 modularization PR-1 (scaffold).

ALL ACTUAL CODE still lives in `legacy.py`. This `__init__.py`
dynamically re-exports every top-level name from `legacy` so the test
contract is preserved exactly:

    import app as A
    A.app, A.engine, A.Session, A.Source, A.JobPosting,
    A.parse_document, A._uid, A._logj, A._mw_session, ...

Why dynamic instead of an explicit import list?
  legacy.py has >300 module-level names (models, schemas, enums,
  routers, helpers — public AND private). The tests reach for many
  private names: _uid, _logj, _REQ_ID, _classify_header,
  _ROLE_AT_COMPANY, _mw_session, _P8_RL, etc. A hand-maintained list
  would inevitably drift; `dir(legacy)` stays correct automatically.

PR-2..PR-N PLAN (one PR at a time, tests green before+after each):
  1. Extract `core/observability.py`  — _log, _logj, _REQ_ID
  2. Extract `core/config.py`         — validate_config, AUTH_ENABLED, etc.
  3. Extract `core/identity.py`       — _uid, DEFAULT_UID, DEFAULT_USER_EMAIL
  4. Extract `db/engine.py`           — engine, get_session, _ensure_additive_columns
  5. Extract `modules/auth/`          — User, _hash_pw, _verify_pw, auth_router
  6. Extract `modules/sources/`       — Source, SourceRef, sources_router
  7. Extract `modules/profile_truth/` — ProfileClaim, extract_claims, etc.
  8. ... (jobs, strategies, scoring, packages, research, applications,
          notifications) — one module per PR.

When a chunk moves OUT of legacy.py:
  - The new module file contains the actual definitions.
  - legacy.py imports those names back from the new file so its own
    internal references stay valid.
  - This __init__.py needs NO changes — dir(legacy) naturally includes
    everything legacy imports.

Rules for contributors:
  - DO NOT add new code to legacy.py. New code goes in a proper module.
  - DO NOT remove names from the `import app; app.X` surface without
    updating tests in the same PR. The contract is load-bearing.
  - Uvicorn entry remains `uvicorn app:app` — the FastAPI instance
    comes through `legacy.app` → re-exported here as `app`.
"""
from . import legacy as _legacy

# Names that are part of every Python module's machinery and must NOT be
# copied into the package namespace (would shadow the package's own).
_PKG_DUNDERS = frozenset({
    "__name__", "__doc__", "__package__", "__loader__", "__spec__",
    "__file__", "__builtins__", "__path__", "__cached__",
})

# Dynamically copy every top-level attribute from legacy.py into THIS
# package's namespace — public AND private.
for _name in dir(_legacy):
    if _name in _PKG_DUNDERS:
        continue
    globals()[_name] = getattr(_legacy, _name)

del _name, _PKG_DUNDERS
'''


def _stub(qualified_name: str) -> str:
    """Placeholder docstring for an empty scaffold module."""
    return (
        f'"""Aptiro backend module: `{qualified_name}`.\n\n'
        f'Phase 9 scaffold — currently empty.\n\n'
        f'Content moves here from `backend/app/legacy.py` in a future\n'
        f'PR (one module per PR). See PHASE9_NEXT_STEPS.md.\n"""\n'
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def info(msg: str) -> None:
    print(msg)


def fail(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _state() -> str:
    """Return 'unapplied' | 'applied' | 'mixed' | 'missing'."""
    ffile = APP_FILE.is_file()
    fpkg = APP_PKG.is_dir() and LEGACY_FILE.exists() and INIT_FILE.exists()
    if fpkg and not ffile:
        return "applied"
    if ffile and not fpkg:
        return "unapplied"
    if ffile and fpkg:
        return "mixed"
    return "missing"


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------
if not BACKEND.is_dir():
    fail(
        f"{BACKEND} not found.\n"
        f"Run this script from the project root (the directory that\n"
        f"contains `backend/`), e.g.:\n\n"
        f"  cd /Users/samswamynathan/projects/Aptiro\n"
        f"  python3 phase9_modularize.py\n"
    )


# ---------------------------------------------------------------------------
# Revert
# ---------------------------------------------------------------------------
if args.revert:
    st = _state()
    if st in ("unapplied", "missing"):
        info("Nothing to revert — backend/app.py is already in file form.")
        sys.exit(0)
    if st == "mixed":
        fail(f"Both {APP_FILE} and {APP_PKG} exist. Resolve manually first.")

    info("Reverting Phase 9 PR-1...")
    if args.dry_run:
        info(f"  [dry-run] mv {LEGACY_FILE.relative_to(ROOT)} "
             f"→  {APP_FILE.relative_to(ROOT)}")
        info(f"  [dry-run] rm -r {APP_PKG.relative_to(ROOT)}")
        sys.exit(0)

    shutil.move(str(LEGACY_FILE), str(APP_FILE))
    shutil.rmtree(APP_PKG)
    info(f"  ✓ restored  {APP_FILE.relative_to(ROOT)}")
    info(f"  ✓ removed   {APP_PKG.relative_to(ROOT)}")
    info(
        "\nRevert complete.\n"
        "  cd backend && . .venv/bin/activate && pytest -q\n"
    )
    sys.exit(0)


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------
st = _state()

if st == "applied":
    info("Phase 9 PR-1 already applied — nothing to do.")
    info(f"  package : {APP_PKG.relative_to(ROOT)}")
    info(f"  legacy  : {LEGACY_FILE.relative_to(ROOT)}")
    info(f"  __init__: {INIT_FILE.relative_to(ROOT)}")
    sys.exit(0)

if st == "mixed":
    fail(
        f"Both {APP_FILE.relative_to(ROOT)} and "
        f"{APP_PKG.relative_to(ROOT)} exist simultaneously.\n"
        f"Python prefers the package, so the file is being silently ignored,\n"
        f"but this is an ambiguous state. To fix:\n"
        f"  • If PR-1 is partially applied: rm backend/app.py manually,\n"
        f"    then re-run this script.\n"
        f"  • If you want to start over: python3 phase9_modularize.py --revert"
    )

if st == "missing":
    fail(f"Neither {APP_FILE.relative_to(ROOT)} nor a valid package at\n"
         f"{APP_PKG.relative_to(ROOT)} was found. Cannot proceed.")

# State: "unapplied" — proceed.

# --- Step 1: read existing backend/app.py ---------------------------------
info(f"Step 1/5  Reading {APP_FILE.relative_to(ROOT)}...")
legacy_src = APP_FILE.read_text()
nlines = legacy_src.count("\n") + (0 if legacy_src.endswith("\n") else 1)
info(f"          {len(legacy_src):,} chars  ~{nlines:,} lines")

# Sanity-check a few markers that must exist in the real app.py.
REQUIRED_MARKERS = [
    "app = FastAPI(",
    "class Source(",
    "def get_session(",
]
missing_markers = [m for m in REQUIRED_MARKERS if m not in legacy_src]
if missing_markers:
    fail(
        f"{APP_FILE.relative_to(ROOT)} does not look like the real Aptiro\n"
        f"backend/app.py (missing markers: {missing_markers}).\n"
        f"Make sure you are running this script from the project root."
    )
info(f"          ✓ content markers validated (FastAPI app, Source, get_session)")

if args.dry_run:
    info("\nStep 2/5  [dry-run] Would create scaffold:")
    for d in SCAFFOLD_DIRS:
        info(f"           mkdir  {d.relative_to(ROOT)}/")
        info(f"           write  {(d / '__init__.py').relative_to(ROOT)}")
    info(f"\nStep 3/5  [dry-run] Would write {LEGACY_FILE.relative_to(ROOT)}")
    info(f"Step 4/5  [dry-run] Would write {INIT_FILE.relative_to(ROOT)}")
    info(f"Step 5/5  [dry-run] Would remove {APP_FILE.relative_to(ROOT)}")
    info("\n[dry-run] No changes written. Remove --dry-run to apply.")
    sys.exit(0)

# --- Step 2: create the scaffold ------------------------------------------
info(f"\nStep 2/5  Building scaffold under {APP_PKG.relative_to(ROOT)}/")
APP_PKG.mkdir(parents=True, exist_ok=True)
for d in SCAFFOLD_DIRS:
    d.mkdir(parents=True, exist_ok=True)
    init = d / "__init__.py"
    if not init.exists():
        rel = d.relative_to(APP_PKG)
        qname = "app." + ".".join(rel.parts)
        init.write_text(_stub(qname))
    info(f"          ✓ {d.relative_to(ROOT)}/")

# --- Step 3: write legacy.py (verbatim copy) ------------------------------
info(f"\nStep 3/5  Writing {LEGACY_FILE.relative_to(ROOT)}")
LEGACY_FILE.write_text(legacy_src)
info(f"          ✓ {len(legacy_src):,} chars written (verbatim copy)")

# --- Step 4: write the __init__.py re-export bridge -----------------------
info(f"\nStep 4/5  Writing {INIT_FILE.relative_to(ROOT)}")
INIT_FILE.write_text(INIT_PY_SRC)
info(f"          ✓ {len(INIT_PY_SRC):,} chars written")

# --- Step 5: remove the now-superseded backend/app.py ---------------------
info(f"\nStep 5/5  Removing {APP_FILE.relative_to(ROOT)}")
APP_FILE.unlink()
info(f"          ✓ removed (content preserved verbatim in legacy.py)")

# --- Verify: byte-compile everything in the new package -------------------
info("\n── Byte-compile verification ─────────────────────────────────")
all_ok = True
py_files = sorted(APP_PKG.rglob("*.py"))
for py in py_files:
    try:
        py_compile.compile(str(py), doraise=True)
        info(f"  ✓ {py.relative_to(BACKEND)}")
    except py_compile.PyCompileError as exc:
        info(f"  ✗ {py.relative_to(BACKEND)}  — {exc}")
        all_ok = False

if not all_ok:
    fail(
        "One or more files failed to byte-compile (see above).\n"
        "The most common cause is a syntax error already present in\n"
        "backend/app.py before this script ran.\n\n"
        "To undo:  python3 phase9_modularize.py --revert",
        code=4,
    )
info(f"  ✓ all {len(py_files)} file(s) compile cleanly.")

# --- Summary --------------------------------------------------------------
info(f"""
═══════════════════════════════════════════════════════════════════════
Phase 9 PR-1 (package scaffold) applied successfully.

Files created:
  backend/app/__init__.py          ← dynamic re-export bridge
  backend/app/legacy.py            ← verbatim copy of old app.py
  backend/app/core/__init__.py     ← scaffold (empty)
  backend/app/db/__init__.py       ← scaffold (empty)
  backend/app/modules/*/           ← 10 scaffold modules (empty)

Files removed:
  backend/app.py                   ← content is now in legacy.py

Zero behaviour change. Run the full test suite to confirm:

  cd backend
  . .venv/bin/activate
  pytest -q

Expected: same test count as before, all green.

To undo at any time:
  cd ..
  python3 phase9_modularize.py --revert

For the next PR, see PHASE9_NEXT_STEPS.md.
═══════════════════════════════════════════════════════════════════════
""")
