#!/usr/bin/env python3
"""Aptiro — Phase 9 PR-11: extract `app.modules.scoring` from `legacy.py`.

Per ROADMAP.md / PHASE9_NEXT_STEPS.md, the rule for every Phase-9 PR is:
    one PR = one moved module, ZERO behaviour change, tests green
    before AND after.

PR-11 moves the deterministic scoring function(s) out of
`backend/app/legacy.py` into `backend/app/modules/scoring/__init__.py`.
`legacy.py` then RE-IMPORTS the moved names so the test contract
(`import app as A; A.score_job(...)`, `A._structured_requirements(...)`)
is unchanged, and PR-10's `modules/strategies` (which does
`from app.legacy import ... score_job ...`) keeps working untouched.

────────────────────────────────────────────────────────────────────────
WHY THIS SCRIPT IS WRITTEN THE WAY IT IS
────────────────────────────────────────────────────────────────────────
`score_job` is the most entangled function in the codebase. Rather than
hardcode its body (and risk a stale copy) or hand-guess its dependency
list (and risk a NameError at boot), this script is *correct by
construction against YOUR actual file*:

  1. It reads your real `legacy.py` and locates each target function as a
     module-level def via the `ast` module (exact source span — robust to
     whatever the body contains).
  2. It lifts each function VERBATIM into the new module.
  3. It computes each function's free (global) names with `ast`, then
     imports exactly those that exist in your app's *live* namespace from
     `app.legacy`. Nothing is guessed; if a free name resolves to neither
     a builtin nor a name in `app`, the script ABORTS before writing.
  4. It inserts the re-import in legacy AT THE LATER def's position, so
     every dependency (defined above it) is already in scope when the new
     module loads — the same ordering trick PR-7/PR-9/PR-10 relied on.
  5. It validates: byte-compile, `import app`, then the full pytest suite.
  6. On ANY failure it RESTORES the exact prior bytes of both files and
     exits non-zero. It cannot leave you half-applied.

It is idempotent (re-running after success is a no-op) and supports
--dry-run (compute + print the plan, write nothing) and --revert
(git-checkout the two files back to HEAD).

Run from the PROJECT ROOT (the directory that contains `backend/`):

    cd /Users/samswamynathan/projects/Aptiro
    python3 phase9_pr11_scoring.py --dry-run     # inspect the plan
    python3 phase9_pr11_scoring.py               # apply + validate
    python3 phase9_pr11_scoring.py --revert      # undo via git checkout
"""

import argparse
import ast
import builtins
import json
import pathlib
import py_compile
import shutil
import subprocess
import sys
import tempfile

# ───────────────────────────────────────────────────────────────────────
# Configuration
# ───────────────────────────────────────────────────────────────────────
# Functions PR-11 moves into modules/scoring, per the roadmap table.
#
#   • The roadmap (PHASE9_NEXT_STEPS.md) lists BOTH of these.
#   • The CODE shows `_structured_requirements` is actually a job-
#     requirements parser used by `import_job` (modules/jobs), and
#     modules/jobs' docstring earmarks it for the future "parsing PR".
#
# Default below = roadmap-table behaviour (move both). If you'd rather
# leave the parser for the parsing PR and keep `scoring` clean, change to:
#       MOVE = ["score_job"]
MOVE = ["score_job", "_structured_requirements"]

MARKER = "APTIRO_PHASE9_PR11_SCORING_MARKER"

ROOT = pathlib.Path.cwd()
BACKEND = ROOT / "backend"
LEGACY = BACKEND / "app" / "legacy.py"
SCORING = BACKEND / "app" / "modules" / "scoring" / "__init__.py"

# venv python (Mac venv layout); falls back to current interpreter.
_VENV_PY = BACKEND / ".venv" / "bin" / "python"
VENV_PY = str(_VENV_PY) if _VENV_PY.exists() else sys.executable


# ───────────────────────────────────────────────────────────────────────
# Small helpers
# ───────────────────────────────────────────────────────────────────────
def fail(msg, code=2):
    print(f"\n[PR-11] ABORT: {msg}\n", file=sys.stderr)
    sys.exit(code)


def info(msg):
    print(f"[PR-11] {msg}")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


# ───────────────────────────────────────────────────────────────────────
# AST: locate functions + derive free (global) names
# ───────────────────────────────────────────────────────────────────────
def find_funcs(tree, names):
    """Return {name: FunctionDef} for module-level defs matching `names`."""
    found = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name in names:
            found[node.name] = node
    return found


class _Bindings(ast.NodeVisitor):
    """Collect every name BOUND anywhere inside a function subtree:
    parameters, assignment targets, for/with/except/comprehension targets,
    walrus targets, and nested def/class names. Over-approximating 'bound'
    is safe here: the two target functions are flat, so no real free name
    is missed. The validation+revert step is the backstop regardless."""

    def __init__(self):
        self.bound = set()

    def _args(self, a):
        for x in (a.posonlyargs + a.args + a.kwonlyargs):
            self.bound.add(x.arg)
        if a.vararg:
            self.bound.add(a.vararg.arg)
        if a.kwarg:
            self.bound.add(a.kwarg.arg)

    def visit_FunctionDef(self, n):
        self.bound.add(n.name)
        self._args(n.args)
        self.generic_visit(n)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, n):
        self._args(n.args)
        self.generic_visit(n)

    def visit_ClassDef(self, n):
        self.bound.add(n.name)
        self.generic_visit(n)

    def visit_Name(self, n):
        if isinstance(n.ctx, (ast.Store, ast.Del)):
            self.bound.add(n.id)
        self.generic_visit(n)

    def visit_arg(self, n):
        self.bound.add(n.arg)
        self.generic_visit(n)

    def visit_ExceptHandler(self, n):
        if n.name:
            self.bound.add(n.name)
        self.generic_visit(n)

    def visit_Global(self, n):
        for nm in n.names:
            self.bound.add(nm)

    visit_Nonlocal = visit_Global


def free_names(node):
    """All names LOADED in the subtree that are not bound within it."""
    b = _Bindings()
    b.visit(node)
    loaded = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
            loaded.add(sub.id)
    return loaded - b.bound


def live_app_namespace():
    """Authoritative list of names importable from app.legacy — the live
    `app` namespace (the package __init__ proxies to legacy)."""
    out = run([VENV_PY, "-c", "import app, json; print(json.dumps(dir(app)))"],
              cwd=str(BACKEND))
    if out.returncode != 0:
        fail("`import app` failed in your venv BEFORE any change was made — "
             "your working tree is not green to start from.\n"
             f"stderr:\n{out.stderr.strip()}", code=3)
    try:
        return set(json.loads(out.stdout.strip().splitlines()[-1]))
    except Exception as e:  # noqa: BLE001
        fail(f"could not read app namespace: {e}\nstdout:\n{out.stdout}")


# ───────────────────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute and print the plan; write nothing.")
    ap.add_argument("--revert", action="store_true",
                    help="git checkout legacy.py + scoring/__init__.py to HEAD.")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite scoring/__init__.py even if it does not "
                         "look like a Phase-9 scaffold.")
    args = ap.parse_args()

    # --- preflight ------------------------------------------------------
    if not BACKEND.is_dir() or not LEGACY.is_file():
        fail("run me from the project root (the folder containing `backend/`). "
             f"Looked for: {LEGACY}")

    if args.revert:
        info("Reverting backend/app/legacy.py and modules/scoring via git …")
        r = run(["git", "checkout", "--",
                 "backend/app/legacy.py",
                 "backend/app/modules/scoring/__init__.py"], cwd=str(ROOT))
        if r.returncode != 0:
            fail("git checkout failed:\n" + r.stderr)
        info("Reverted to HEAD. Done.")
        return

    legacy_src = LEGACY.read_text()

    # idempotency: already applied?
    if MARKER in legacy_src:
        info("PR-11 marker already present in legacy.py — nothing to do. "
             "(Already applied.)")
        return

    scoring_src = SCORING.read_text() if SCORING.exists() else ""
    if MARKER in scoring_src:
        info("PR-11 marker already present in scoring module — nothing to do.")
        return
    looks_scaffold = ("scaffold" in scoring_src.lower()
                      or len(scoring_src.strip()) < 400)
    if not looks_scaffold and not args.force:
        fail("backend/app/modules/scoring/__init__.py is not empty and does "
             "not look like a Phase-9 scaffold. Refusing to clobber it. "
             "Re-run with --force only if you are sure.")

    # --- parse + locate target functions --------------------------------
    try:
        tree = ast.parse(legacy_src)
    except SyntaxError as e:
        fail(f"legacy.py does not parse: {e}")

    funcs = find_funcs(tree, set(MOVE))
    missing = [n for n in MOVE if n not in funcs]
    if missing:
        fail("expected module-level function(s) not found in legacy.py: "
             f"{', '.join(missing)}.\nEither PR-11 was already done, or the "
             "starting state is not what PR-11 expects. No changes made.")

    src_lines = legacy_src.splitlines(keepends=True)

    # verbatim source span (1-based ast linenos → 0-based slice)
    spans = {n: (f.lineno - 1, f.end_lineno) for n, f in funcs.items()}
    blocks = {n: "".join(src_lines[s:e]) for n, (s, e) in spans.items()}

    # --- derive the from-legacy import set ------------------------------
    builtin_names = set(dir(builtins))
    moved = set(MOVE)
    all_free = set()
    for n in MOVE:
        all_free |= free_names(funcs[n])
    # the moved funcs may reference each other; they are defined IN the new
    # module, so don't import them from legacy.
    all_free -= moved
    all_free -= builtin_names

    live = live_app_namespace()
    from_legacy = sorted(all_free & live)
    unresolved = sorted(all_free - live)
    if unresolved:
        fail("these free names in the moved function(s) resolve to neither a "
             "builtin nor a name in `app`:\n    "
             + ", ".join(unresolved)
             + "\nThe new module would NameError at runtime. No changes made. "
               "(If these are stdlib/3rd-party, the moved code likely uses an "
               "import that lives elsewhere in legacy — tell Claude and we'll "
               "widen the extraction.)")

    # --- build the new scoring module text ------------------------------
    funcs_in_file_order = sorted(MOVE, key=lambda n: spans[n][0])
    imp = ("from app.legacy import (\n    "
           + ",\n    ".join(from_legacy)
           + ",\n)\n") if from_legacy else ""
    module_text = (
        '"""Aptiro backend module: `app.modules.scoring` (Phase 9 PR-11).\n\n'
        "The deterministic scoring function(s) extracted verbatim from\n"
        "`legacy.py`. `legacy.py` re-imports these names so the test contract\n"
        "(`import app as A; A.score_job`, `A._structured_requirements`) and\n"
        "PR-10's `modules/strategies` (which imports `score_job` from\n"
        "`app.legacy`) are unchanged.\n\n"
        "Every helper/constant these functions depend on still lives in\n"
        "`legacy.py` and is imported below. legacy triggers\n"
        "`from app.modules.scoring import ...` only AFTER all of those are\n"
        "defined, so there is no circular-import or ordering hazard.\n"
        f"\n# {MARKER}\n"
        '"""\n\n'
        + imp + "\n"
        "# ═══════════════════════════════════════════════════════════════\n"
        "#  Extracted verbatim from legacy.py (Phase 9 PR-11)\n"
        "# ═══════════════════════════════════════════════════════════════\n\n"
        + "\n\n".join(blocks[n].rstrip() + "\n" for n in funcs_in_file_order)
    )

    # --- compute the legacy patch (replace later def with the import) ---
    later = max(MOVE, key=lambda n: spans[n][0])
    later_s, later_e = spans[later]
    import_line = (
        f"# {' / '.join(MOVE)} extracted to app.modules.scoring "
        "(Phase 9 PR-11); re-imported here.\n"
        f"from app.modules.scoring import {', '.join(MOVE)}  # noqa: E402,F401\n"
        f"# {MARKER}\n"
    )

    # back-to-front edits keep slice indices valid
    edits = []  # (start, end, replacement)
    edits.append((later_s, later_e, import_line))
    for n in MOVE:
        if n == later:
            continue
        s, e = spans[n]
        edits.append((s, e,
                      f"# {n} extracted to app.modules.scoring "
                      "(Phase 9 PR-11); re-imported below.\n"))
    edits.sort(key=lambda t: t[0], reverse=True)

    new_lines = list(src_lines)
    for s, e, rep in edits:
        new_lines[s:e] = [rep]
    new_legacy = "".join(new_lines)

    # --- dry run --------------------------------------------------------
    info(f"Project root      : {ROOT}")
    info(f"Python (venv)     : {VENV_PY}")
    info(f"Moving            : {', '.join(MOVE)}")
    for n in MOVE:
        s, e = spans[n]
        info(f"  {n:<24} legacy.py lines {s + 1}–{e} "
             f"({e - s} lines, {len(blocks[n])} chars)")
    info(f"Re-import inserted at: line {later_s + 1} (where `{later}` was)")
    info(f"from app.legacy import ({len(from_legacy)}): "
         + (", ".join(from_legacy) if from_legacy else "<none>"))

    if args.dry_run:
        info("\n--- new scoring/__init__.py (preview, first 60 lines) ---")
        print("\n".join(module_text.splitlines()[:60]))
        info("[dry-run] No files written.")
        return

    # --- apply with backup + auto-revert --------------------------------
    bak = pathlib.Path(tempfile.mkdtemp(prefix="aptiro_pr11_"))
    legacy_bak = bak / "legacy.py"
    scoring_bak = bak / "scoring__init__.py"
    legacy_bak.write_text(legacy_src)
    scoring_bak.write_text(scoring_src)

    def revert(reason, detail=""):
        LEGACY.write_text(legacy_src)
        SCORING.write_text(scoring_src)
        shutil.rmtree(bak, ignore_errors=True)
        print(f"\n[PR-11] REVERTED — files restored to their prior state.\n"
              f"[PR-11] Reason: {reason}", file=sys.stderr)
        if detail:
            print(detail, file=sys.stderr)
        sys.exit(1)

    info("\nWriting modules/scoring/__init__.py and patching legacy.py …")
    SCORING.parent.mkdir(parents=True, exist_ok=True)
    SCORING.write_text(module_text)
    LEGACY.write_text(new_legacy)

    # byte-compile both
    for f in (SCORING, LEGACY):
        try:
            py_compile.compile(str(f), doraise=True)
        except py_compile.PyCompileError as e:
            revert("byte-compile failed", str(e))

    # import the app
    imp_check = run([VENV_PY, "-c",
                     "import app as A; "
                     "assert callable(A.score_job); "
                     + "".join(f"assert callable(A.{n}); " for n in MOVE)
                     + "print('import-ok')"], cwd=str(BACKEND))
    if imp_check.returncode != 0 or "import-ok" not in imp_check.stdout:
        revert("`import app` / symbol check failed after patch",
               imp_check.stderr or imp_check.stdout)

    # full suite
    info("Running pytest -q (this is the gate) …")
    test = run([VENV_PY, "-m", "pytest", "-q"], cwd=str(BACKEND))
    tail = "\n".join((test.stdout + test.stderr).strip().splitlines()[-8:])
    if test.returncode != 0:
        revert("pytest failed after patch", tail)

    # success
    shutil.rmtree(bak, ignore_errors=True)
    print("\n" + "═" * 72)
    info("PR-11 applied and validated. Test suite is GREEN.")
    print("═" * 72)
    print(tail)
    print(f"""
Files changed:
  backend/app/modules/scoring/__init__.py   ← {', '.join(MOVE)} (verbatim)
  backend/app/legacy.py                     ← functions replaced by re-import

Commit:
  git add backend/app/modules/scoring/__init__.py backend/app/legacy.py
  git add phase9_pr11_scoring.py
  git commit -m "Phase 9 PR-11: extract app.modules.scoring ({', '.join(MOVE)})"
  git push origin main
""")


if __name__ == "__main__":
    main()
