#!/usr/bin/env python3
"""Aptiro — Phase 9 PR-10: extract `backend/app/modules/strategies/__init__.py`.

What this does
──────────────
legacy.py accumulated TWO copies of the Phase-4 multi-strategy block:

  * Copy 1  (~lines 3817–4366): introduced by a "# Preset definitions"
    comment + `STRATEGY_PRESETS: List[dict] = [`. Its `strategies_router`
    is reassigned by Copy 2 before the single `app.include_router(...)`,
    so Copy 1 is DEAD code that never serves a request.

  * Copy 2  (lines 4367–4719): the LIVE block — `STRATEGY_PRESETS = [`,
    the strategy schemas, `strategies_router = APIRouter(prefix=
    "/api/strategies")`, the preview helpers, every endpoint, and the
    seed-presets route. This is what `app.include_router(strategies_
    router)` (line 4720) actually registers.

PR-10 in one surgical edit:
  1. Extract Copy 2 verbatim into `modules/strategies/__init__.py`.
  2. Delete the dead Copy 1 (and its leading comment) in the same span.
  3. Replace the whole [Copy 1 start … just before include_router] span
     with ONE import block that pulls the extracted symbols back into
     legacy's namespace.
  4. Keep `app.include_router(strategies_router)` exactly where it is.

What stays in legacy.py (imported by the module, NOT moved):
  Strategy (model), StrategyUpsert, StrategyRead, score_job, the singular
  /api/strategy router, _active_strategy, _strategy_read, Aggressiveness,
  WorkMode, DEFAULT_WEIGHTS.

Why this is load-order-safe (no lazy bridges)
─────────────────────────────────────────────
legacy imports this module at the Copy-1 site (~line 3817). By then
legacy has already defined every dependency the module imports:
  Strategy@286, StrategyUpsert@982, StrategyRead@997, score_job@1573,
  DEFAULT_WEIGHTS@279, Aggressiveness@160, WorkMode(<286), _now(<286).
The remaining deps come from already-fully-loaded sibling modules
(app.core.identity._uid, app.db.engine.get_session,
app.modules.jobs.JobPosting), so there is no circular-import hazard.

Fail-closed
───────────
  * Idempotency marker check (re-runs are a no-op).
  * Anchor uniqueness validated BEFORE any write; abort if off.
  * legacy.py backed up to legacy.py.pr10_backup.
  * Patched legacy.py and the new module are byte-compiled; on any
    failure legacy.py is restored from memory and the run aborts.

Usage (run from the repo root, the dir containing backend/)
  python3 phase9_pr10_strategies.py --dry-run     # validate only
  python3 phase9_pr10_strategies.py               # apply
"""

from __future__ import annotations

import py_compile
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path.cwd()
LEGACY = ROOT / "backend" / "app" / "legacy.py"
MOD_DIR = ROOT / "backend" / "app" / "modules" / "strategies"
MOD_FILE = MOD_DIR / "__init__.py"
BACKUP = ROOT / "backend" / "app" / "legacy.py.pr10_backup"

MARKER = "APTIRO_PHASE9_PR10_STRATEGIES_MARKER"
DRY_RUN = "--dry-run" in sys.argv[1:]

# Anchors (verified against legacy.py).
COPY1_PRESETS = "STRATEGY_PRESETS: List[dict] = ["      # Copy 1 (annotated)
COPY2_PRESETS = "\nSTRATEGY_PRESETS = ["                 # Copy 2 (live), col 0
INCLUDE_CALL = "\napp.include_router(strategies_router)\n"

MOD_HEADER = '''"""Aptiro backend module: `app.modules.strategies` (Phase 9 PR-10).

The multi-strategy domain — the six tuned presets, the strategy Pydantic
schemas, the plural `/api/strategies` router with all of its endpoints,
the preview helpers, and the seed-presets route — extracted verbatim
from `legacy.py`. The dead duplicate copy legacy.py had accumulated was
removed in the same change.

The `Strategy` table model and the singular `StrategyUpsert` /
`StrategyRead` schemas stay in `legacy.py`; this module imports them.
legacy imports this module only AFTER those symbols (and `score_job`)
are defined, so every dependency below resolves at import time — no lazy
bridges, no circular-import hazard.
"""

from datetime import datetime  # noqa: F401
from typing import List, Optional  # noqa: F401

from fastapi import APIRouter, Depends, HTTPException  # noqa: F401
from pydantic import BaseModel  # noqa: F401
from sqlmodel import Session, select  # noqa: F401

from app.core.identity import _uid  # noqa: F401
from app.db.engine import get_session  # noqa: F401
from app.modules.jobs import JobPosting  # noqa: F401
from app.legacy import (  # noqa: F401
    Strategy, StrategyUpsert, StrategyRead,
    Aggressiveness, WorkMode, DEFAULT_WEIGHTS, score_job, _now,
    _active_strategy, _strategy_read,
)


# ═══════════════════════════════════════════════════════════════════════
#  Extracted verbatim from legacy.py (Phase 9 PR-10)
# ═══════════════════════════════════════════════════════════════════════
'''


def info(msg): print(f"  {msg}")
def ok(msg): print(f"  \u2713 {msg}")


def die(msg, code=1):
    print(f"\n  \u2717 {msg}\n", file=sys.stderr)
    sys.exit(code)


def _line_start(src, idx):
    return src.rfind("\n", 0, idx) + 1


def _compiles(text):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(text)
        tmp = f.name
    try:
        py_compile.compile(tmp, doraise=True)
        return True, ""
    except py_compile.PyCompileError as e:
        return False, str(e)
    finally:
        Path(tmp).unlink(missing_ok=True)


# Names the extracted block defines that legacy should re-import:
# classes, ALL_CAPS constants, _underscore helpers, and strategies_router.
# (Plural endpoint functions are reached only via their @strategies_router
# decorator, so legacy never needs them by name.)
_CLASS_RE = re.compile(r"^class\s+([A-Za-z_]\w*)", re.M)
_CONST_RE = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=", re.M)
_HELP_RE = re.compile(r"^def\s+(_[A-Za-z_]\w*)", re.M)


def _reimport_names(body):
    names, seen = [], set()
    for rx in (_CLASS_RE, _CONST_RE, _HELP_RE):
        for m in rx.finditer(body):
            n = m.group(1)
            if n not in seen:
                seen.add(n)
                names.append(n)
    if "strategies_router" not in seen:
        names.append("strategies_router")
    return names


def main():
    print("\nAptiro — Phase 9 PR-10: extract modules/strategies/")
    print("=" * 63)

    if not LEGACY.exists():
        die(f"legacy.py not found at {LEGACY} — run from the repo root.")
    if not MOD_DIR.exists():
        die(f"scaffold dir missing: {MOD_DIR} (expected from PR-1).")

    src = LEGACY.read_text()
    if MARKER in src:
        ok("PR-10 marker already present — nothing to do (idempotent).")
        sys.exit(0)

    # ── Validate anchors ────────────────────────────────────────────────
    print("\nStep 1/5  Validating anchors")
    if src.count(COPY1_PRESETS) != 1:
        die(f"expected exactly 1 Copy-1 presets `{COPY1_PRESETS}`, "
            f"found {src.count(COPY1_PRESETS)}.")
    if src.count(COPY2_PRESETS) != 1:
        die(f"expected exactly 1 Copy-2 presets `STRATEGY_PRESETS = [`, "
            f"found {src.count(COPY2_PRESETS)}.")
    if src.count(INCLUDE_CALL) != 1:
        die(f"expected exactly 1 `app.include_router(strategies_router)` "
            f"at column 0, found {src.count(INCLUDE_CALL)}.")
    ok("Copy-1 presets x1, Copy-2 presets x1, include_router x1")

    # ── Locate spans ────────────────────────────────────────────────────
    # Copy 1 start: walk back from its presets over the leading comment.
    c1_idx = src.find(COPY1_PRESETS)
    c1_start = _line_start(src, c1_idx)
    while True:
        prev_end = c1_start - 1                       # the '\n' before us
        if prev_end < 0:
            break
        prev_start = src.rfind("\n", 0, prev_end) + 1
        if src[prev_start:prev_end].lstrip().startswith("#"):
            c1_start = prev_start
        else:
            break

    # Copy 2 body: from its presets line up to the include_router line.
    c2_body_start = _line_start(src, src.find(COPY2_PRESETS) + 1)
    delete_end = src.find(INCLUDE_CALL) + 1            # keep the include line

    if not (c1_start < c2_body_start < delete_end):
        die("span ordering check failed (Copy1 < Copy2 < include).")

    copy2_body = src[c2_body_start:delete_end].rstrip() + "\n"
    deleted_span = src[c1_start:delete_end]
    ok(f"Copy-1 (dead) span : {c2_body_start - c1_start:>6} chars")
    ok(f"Copy-2 (extract)   : {delete_end - c2_body_start:>6} chars")
    ok(f"total removed      : {len(deleted_span):>6} chars")

    # ── Build module + patched legacy ───────────────────────────────────
    print("\nStep 2/5  Building module + import block")
    module_text = MOD_HEADER + copy2_body

    names = _reimport_names(copy2_body)
    if "strategies_router" not in names or "STRATEGY_PRESETS" not in names:
        die("derived import list missing core symbols — aborting.")
    lines, line = [], "    "
    for nm in names:
        piece = nm + ", "
        if len(line) + len(piece) > 76:
            lines.append(line.rstrip())
            line = "    "
        line += piece
    if line.strip():
        lines.append(line.rstrip())
    import_block = (
        f"# {MARKER}\n"
        f"# Phase-4 multi-strategy domain extracted to modules/strategies\n"
        f"# (Phase 9 PR-10); the dead duplicate block was removed here too.\n"
        f"from app.modules.strategies import (  # noqa: F401\n"
        + "\n".join(lines) + "\n)\n\n\n"
    )
    patched = src[:c1_start] + import_block + src[delete_end:]
    ok(f"module: {len(module_text):,} chars, {module_text.count(chr(10))} lines")
    ok(f"legacy: {len(src):,} -> {len(patched):,} chars "
       f"(\u0394 {len(patched) - len(src):+,})")
    ok(f"re-import names ({len(names)}): {', '.join(names)}")

    # ── Compile-check both artifacts in memory ──────────────────────────
    print("\nStep 3/5  Byte-compiling both artifacts")
    okm, err = _compiles(module_text)
    if not okm:
        die(f"generated module does NOT compile:\n{err}", code=4)
    ok("modules/strategies/__init__.py compiles")
    okl, err = _compiles(patched)
    if not okl:
        die(f"patched legacy.py does NOT compile:\n{err}", code=4)
    ok("patched legacy.py compiles")

    if DRY_RUN:
        print("\n[dry-run] anchors valid, both artifacts compile. "
              "No files written.")
        sys.exit(0)

    # ── Write ───────────────────────────────────────────────────────────
    print("\nStep 4/5  Writing files")
    BACKUP.write_text(src)
    ok(f"backed up -> {BACKUP.name}")
    MOD_FILE.write_text(module_text)
    ok(f"wrote {MOD_FILE.relative_to(ROOT)}")
    LEGACY.write_text(patched)
    ok(f"patched {LEGACY.relative_to(ROOT)}")

    # ── Structural self-checks (auto-rollback) ──────────────────────────
    print("\nStep 5/5  Structural invariants")
    final = LEGACY.read_text()
    checks = [
        ("PR-10 marker present", MARKER in final),
        ("imports from modules.strategies",
         "from app.modules.strategies import" in final),
        ("no Copy-1 presets left", COPY1_PRESETS not in final),
        ("no Copy-2 presets left", COPY2_PRESETS not in final),
        ("no strategies_router decl left",
         "strategies_router = APIRouter(" not in final),
        ("include_router kept",
         "app.include_router(strategies_router)" in final),
        ("Strategy model still in legacy",
         "class Strategy(SQLModel, table=True):" in final),
        ("StrategyUpsert still in legacy",
         "class StrategyUpsert(BaseModel):" in final),
        ("StrategyRead still in legacy",
         "class StrategyRead(StrategyUpsert):" in final),
        ("singular strategy_router still in legacy",
         'strategy_router = APIRouter(prefix="/api/strategy"' in final),
        ("_active_strategy still in legacy", "def _active_strategy(" in final),
        ("score_job still in legacy", "def score_job(" in final),
        ("SavedSearch (Phase 5) intact", "class SavedSearch(" in final),
    ]
    tick, cross = "\u2713", "\u2717"
    all_ok = True
    for label, result in checks:
        print(f"  {tick if result else cross} {label}")
        all_ok = all_ok and result
    if not all_ok:
        LEGACY.write_text(src)
        die("structural checks FAILED — rolled legacy.py back from memory.\n"
            "  (modules/strategies/__init__.py was written; inspect it.)",
            code=5)

    print("\n" + "=" * 63)
    print("Phase 9 PR-10 applied. Run the suite:\n")
    print("  cd backend && . .venv/bin/activate && pytest -q")
    print("\nExpected: 218 passed, all green.\n")
    print("If GREEN — commit + push:")
    print("  cd ..")
    print("  rm backend/app/legacy.py.pr10_backup phase9_pr10_strategies.py")
    print("  git add -A")
    print('  git commit -m "Phase 9 PR-10: extract modules/strategies/ '
          '(+ remove dead duplicate block)"')
    print("  git push origin main\n")
    print("If RED — roll back:")
    print("  cp backend/app/legacy.py.pr10_backup backend/app/legacy.py")
    print("  cd backend && . .venv/bin/activate && pytest -q")
    print("=" * 63 + "\n")


if __name__ == "__main__":
    main()
