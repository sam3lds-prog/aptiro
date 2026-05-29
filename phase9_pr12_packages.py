#!/usr/bin/env python3
"""Aptiro — Phase 9 PR-12: extract the packages HTTP layer into
`backend/app/modules/packages/__init__.py`.

Per ROADMAP.md / PHASE9_NEXT_STEPS.md, every Phase-9 PR obeys one rule:

    one PR = one moved module, ZERO behaviour change, tests green
    before AND after.

────────────────────────────────────────────────────────────────────────
WHAT PR-12 MOVES (and, deliberately, what it does NOT)
────────────────────────────────────────────────────────────────────────
PR-12 lifts the **packages/runs HTTP layer** out of `legacy.py`:

    packages_router = APIRouter(prefix="/api/packages", ...)
    runs_router     = APIRouter(tags=["runs"])
    + EVERY top-level function decorated by @packages_router.* / @runs_router.*
      (list/create/get package, patch_bullet, orchestrate, the council +
       AI-assist endpoints, export_preview, export_package, get_run, ...)

It does NOT move the SQLModel TABLE models (ApplicationPackage,
PackageBullet, AgentRun, AgentCritique) or their enums, schemas, or the
package-builder helpers. Those are IMPORTED BACK into the new module from
`app.legacy`.

WHY the models stay (the one real hazard in this PR):
  In legacy's model block, `PackageBullet.package: Optional[ApplicationPackage]`
  and `AgentRun.package: Optional[ApplicationPackage]` annotate with the
  ACTUAL class (not a "string" forward-ref). If ApplicationPackage moved
  out and were only re-imported near the end of legacy, those sibling
  models — which stay/are defined earlier — would hit a NameError at class
  definition time. Moving the whole model+agent cluster together could
  avoid that, but only if NO model that remains in legacy annotates one of
  them with a non-string ref; that can't be proven from here. So PR-12
  keeps the models put and moves only the router layer (whose deps are all
  defined earlier in legacy, making the extraction unconditionally
  load-order-safe). A later PR can re-home the models once the model graph
  is confirmed string-ref-clean.

This still delivers exactly what PR-12 is for: "packages router, export."

────────────────────────────────────────────────────────────────────────
WHY THIS SCRIPT IS CORRECT-BY-CONSTRUCTION AGAINST YOUR REAL FILE
────────────────────────────────────────────────────────────────────────
Nothing about the move is hard-coded from a stale copy:

  1. It parses your real `legacy.py` with `ast` and finds the two router
     assignments plus EVERY function whose decorator targets one of them.
     (No need to enumerate endpoint names by hand — they are discovered.)
  2. It lifts each moved statement VERBATIM (decorators included) in source
     order into the new module.
  3. It computes the free (global) names used by the moved code with proper
     per-function scope analysis, then imports `app` in your venv to see
     which of those names actually exist on the live app namespace. Library
     names (fastapi/sqlmodel/stdlib) come from explicit header imports;
     every remaining domain name is imported `from app.legacy import ...`.
     If ANY free name resolves to neither a known library, a builtin, nor a
     name on `app`, the script ABORTS before writing a single byte.
  4. It removes the moved spans from legacy and inserts ONE re-import
     (`from app.modules.packages import packages_router, runs_router`)
     immediately BEFORE `app.include_router(packages_router)`. By that
     point every dependency the new module imports is already defined in
     legacy, so the partial-module import mechanism (same trick PR-7 / PR-10
     used) resolves cleanly with no circular hazard.
  5. It validates: byte-compile both files, `import app`, then the FULL
     pytest suite — all in your venv.
  6. On ANY failure it RESTORES the exact prior bytes of `legacy.py` and
     deletes the half-written module, then exits non-zero. It cannot leave
     you half-applied (the PR-7 `claim_read` incident is exactly the
     failure mode this guards against).

It is idempotent (re-running after success is a no-op), and supports
--dry-run (compute + print the plan, write nothing) and --revert
(git-checkout legacy.py + remove the module).

Run from the PROJECT ROOT (the directory that contains `backend/`):

    cd /Users/samswamynathan/projects/Aptiro
    python3 phase9_pr12_packages.py --dry-run     # inspect the plan
    python3 phase9_pr12_packages.py               # apply + validate
    python3 phase9_pr12_packages.py --revert      # undo via git checkout
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
ROUTER_NAMES = {"packages_router", "runs_router"}
MARKER = "APTIRO_PHASE9_PR12_PACKAGES_MARKER"
# Where to splice the re-import. This line is left in legacy; the import is
# inserted on the line ABOVE it.
INCLUDE_ANCHOR = "app.include_router(packages_router)"

# Test seams: names the test-suite monkeypatches on the `app` package proxy
# (which forwards writes through to app.legacy). The moved code reads these at
# RUN time, so they MUST be resolved through the legacy module on each call —
# NOT bound by value at import time. A plain `from app.legacy import _ai_provider`
# would snapshot the original function, so a stub a test injects on
# app.legacy._ai_provider would never be seen by the moved endpoint. For any
# seam name in this set that the move actually depends on, the generated module
# emits a thin call-time delegating shim instead of a static import. _ai_provider
# is the documented single seam (CHANGES.md, Phase 5: "tests inject stubs here").
SEAM_NAMES = {"_ai_provider"}

ROOT = pathlib.Path.cwd()
BACKEND = ROOT / "backend"
LEGACY = BACKEND / "app" / "legacy.py"
MOD_DIR = BACKEND / "app" / "modules" / "packages"
MOD_FILE = MOD_DIR / "__init__.py"

# venv python (Mac venv layout); falls back to current interpreter.
_VENV_PY = BACKEND / ".venv" / "bin" / "python"
VENV_PY = str(_VENV_PY) if _VENV_PY.exists() else sys.executable

PY_BUILTINS = set(dir(builtins))

# Library names supplied by the new module's OWN header imports. These are
# excluded from the `from app.legacy import ...` block so a name is never
# imported twice. Keep this set EXACTLY in lockstep with MODULE_HEADER.
HEADER_LIB_NAMES = {
    "re", "datetime", "timezone",
    "List", "Optional", "Dict", "Any",
    "APIRouter", "Depends", "HTTPException", "Query", "Response",
    "Path", "Body", "status", "File", "UploadFile", "Form",
    "BaseModel",
    "Session", "SQLModel", "select", "Field", "Relationship",
}

MODULE_HEADER = '''\
"""Aptiro backend module: `app.modules.packages` (Phase 9 PR-12).

The packages/runs HTTP layer, extracted VERBATIM from legacy.py:
  * Router  — packages_router  (prefix=/api/packages)
  * Router  — runs_router      (/api/runs/...)
  * Every endpoint decorated by those two routers — package CRUD, bullet
    patch, the 13-step orchestrator + 5-agent council, the Phase-5 grounded
    AI-assist endpoints, and the export / export-preview endpoints.

Behaviour is identical to pre-PR-12. legacy.py re-imports the two routers
so `app.include_router(packages_router)` / `runs_router` register exactly
the same routes as before, and the test contract
(`import app as A; A.packages_router`, `A.runs_router`) is unchanged.

NOT moved (imported back from legacy by the block below):
  * Table models   — ApplicationPackage, PackageBullet, AgentRun,
                     AgentCritique (their relationship annotations use the
                     actual class, so they must stay co-located for now).
  * Enums + schemas — PackageStatus/BulletStatus/RunStatus/AgentRole,
                     PackageOut/BulletOut/RunOut/... and the AI-assist
                     request/response schemas.
  * Helpers        — build_package, _package_out, _bullet_out, _run_out,
                     _bullet_live_provenance, _unsupported_metrics,
                     _recompute_cover_letter, _ai_provider, _export_model,
                     EXPORT_MEDIA, _get_owned, _active_strategy, score_job,
                     exporting, provenance_color, JobPosting, _uid,
                     get_session, _now, ...

Cross-module load order
───────────────────────
legacy imports this module from the line directly above
`app.include_router(packages_router)`. By then legacy has already defined
every name imported below (models/schemas/helpers are all earlier in the
file, and the export helpers sit just above the include call), so the
partial-module import resolves with no circular hazard.

# {marker}
"""

import re  # noqa: F401
from datetime import datetime, timezone  # noqa: F401
from typing import Any, Dict, List, Optional  # noqa: F401

from fastapi import (  # noqa: F401
    APIRouter, Body, Depends, File, Form, HTTPException, Path, Query,
    Response, UploadFile, status,
)
from pydantic import BaseModel  # noqa: F401
from sqlmodel import (  # noqa: F401
    Field, Relationship, SQLModel, Session, select,
)

'''


# ───────────────────────────────────────────────────────────────────────
# Small helpers
# ───────────────────────────────────────────────────────────────────────
def fail(msg, code=2):
    print(f"\n[PR-12] ABORT: {msg}\n", file=sys.stderr)
    sys.exit(code)


def info(msg):
    print(f"[PR-12] {msg}")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _remove_module_tree():
    """Remove the generated module file and its now-empty package dir.

    Deletes app/modules/packages/__init__.py, drops any __pycache__, and
    removes the package directory itself if it is left empty — so a revert
    or a fail-closed restore leaves the working tree exactly as it was
    before the script ran (no stray empty folder for git to puzzle over).
    """
    if MOD_FILE.exists():
        MOD_FILE.unlink()
    pyc = MOD_DIR / "__pycache__"
    if pyc.exists():
        shutil.rmtree(pyc, ignore_errors=True)
    if MOD_DIR.exists() and not any(MOD_DIR.iterdir()):
        MOD_DIR.rmdir()


# ───────────────────────────────────────────────────────────────────────
# AST: bound-name + free-name analysis
# ───────────────────────────────────────────────────────────────────────
def _args_names(a):
    out = set()
    for arg in list(getattr(a, "posonlyargs", [])) + list(a.args) + \
            list(a.kwonlyargs):
        out.add(arg.arg)
    if a.vararg:
        out.add(a.vararg.arg)
    if a.kwarg:
        out.add(a.kwarg.arg)
    return out


def _bound_in_function(fn):
    """Over-approximate every name BOUND anywhere inside a function subtree:
    params (incl. nested defs/lambdas), assignment / for / with / except /
    comprehension / walrus targets, and nested def/class names. Over-
    approximating 'bound' can only DROP a free name, never invent one — and
    the pytest validation step is the backstop for the rare flat-scope miss.
    """
    bound = set(_args_names(fn.args))

    class B(ast.NodeVisitor):
        def _targets(self, t):
            if isinstance(t, ast.Name):
                bound.add(t.id)
            elif isinstance(t, (ast.Tuple, ast.List)):
                for e in t.elts:
                    self._targets(e)
            elif isinstance(t, ast.Starred):
                self._targets(t.value)

        def visit_Assign(self, n):
            for t in n.targets:
                self._targets(t)
            self.generic_visit(n)

        def visit_AnnAssign(self, n):
            if n.target is not None:
                self._targets(n.target)
            self.generic_visit(n)

        def visit_AugAssign(self, n):
            self._targets(n.target)
            self.generic_visit(n)

        def visit_For(self, n):
            self._targets(n.target)
            self.generic_visit(n)

        visit_AsyncFor = visit_For

        def visit_With(self, n):
            for it in n.items:
                if it.optional_vars is not None:
                    self._targets(it.optional_vars)
            self.generic_visit(n)

        visit_AsyncWith = visit_With

        def visit_ExceptHandler(self, n):
            if n.name:
                bound.add(n.name)
            self.generic_visit(n)

        def visit_NamedExpr(self, n):  # walrus :=
            self._targets(n.target)
            self.generic_visit(n)

        def visit_comprehension(self, n):
            self._targets(n.target)
            self.generic_visit(n)

        def visit_FunctionDef(self, n):
            bound.add(n.name)
            bound.update(_args_names(n.args))
            self.generic_visit(n)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Lambda(self, n):
            bound.update(_args_names(n.args))
            self.generic_visit(n)

        def visit_ClassDef(self, n):
            bound.add(n.name)
            self.generic_visit(n)

        def visit_Import(self, n):
            for al in n.names:
                bound.add((al.asname or al.name).split(".")[0])

        def visit_ImportFrom(self, n):
            for al in n.names:
                bound.add(al.asname or al.name)

    B().visit(fn)
    return bound


def _loaded_names(node):
    return {n.id for n in ast.walk(node)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def free_names_of(node):
    """Free (global) names referenced by a single moved top-level node."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        # Names referenced in decorators are evaluated in the ENCLOSING
        # scope, so they are free regardless of the body's locals.
        deco_free = set()
        for d in node.decorator_list:
            deco_free |= _loaded_names(d)
        return (_loaded_names(node) - _bound_in_function(node)) | deco_free
    if isinstance(node, ast.Assign):
        targets = set()
        for t in node.targets:
            if isinstance(t, ast.Name):
                targets.add(t.id)
        return _loaded_names(node) - targets
    return _loaded_names(node)


# ───────────────────────────────────────────────────────────────────────
# AST: locate the moved statements
# ───────────────────────────────────────────────────────────────────────
def _decorator_router(fn):
    """Return the router name a function is decorated with, or None."""
    for d in fn.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        # @packages_router.get("...") -> Attribute(value=Name('packages_router'))
        if isinstance(target, ast.Attribute) and \
                isinstance(target.value, ast.Name) and \
                target.value.id in ROUTER_NAMES:
            return target.value.id
    return None


def collect_moved(tree):
    """Return module-level nodes to move, in source order:
    the two router-assignments + every function decorated by them."""
    moved = []
    seen_router_assign = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in ROUTER_NAMES:
                    moved.append(node)
                    seen_router_assign.add(t.id)
                    break
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _decorator_router(node) is not None:
                moved.append(node)
    moved.sort(key=lambda n: n.lineno)
    return moved, seen_router_assign


def node_span(src_lines, node):
    """(start_char, end_char) covering decorators..end of node, inclusive of
    the trailing newline. Offsets index into the *joined* source string."""
    start_line = node.lineno
    for d in getattr(node, "decorator_list", []) or []:
        start_line = min(start_line, d.lineno)
    end_line = node.end_lineno
    # convert 1-based inclusive line range to char offsets
    line_starts = src_lines  # precomputed cumulative offsets
    start = line_starts[start_line - 1]
    end = line_starts[end_line]  # start of the line AFTER the node
    return start, end


def line_offsets(src):
    offs = [0]
    for line in src.splitlines(keepends=True):
        offs.append(offs[-1] + len(line))
    return offs


# ───────────────────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute + print the plan; write nothing.")
    ap.add_argument("--revert", action="store_true",
                    help="git checkout legacy.py and remove the module.")
    args = ap.parse_args()

    if not LEGACY.exists():
        fail(f"{LEGACY} not found. Run from the project root "
             f"(the directory containing backend/). PR-1 must be applied.")

    # ---- revert -------------------------------------------------------
    if args.revert:
        r = run(["git", "checkout", "--", str(LEGACY)], cwd=ROOT)
        if r.returncode != 0:
            fail("git checkout legacy.py failed:\n" + r.stderr)
        _remove_module_tree()
        info("Reverted: legacy.py restored, modules/packages/__init__.py removed.")
        return

    src = LEGACY.read_text()

    # ---- idempotency --------------------------------------------------
    if MARKER in src:
        info("PR-12 already applied — nothing to do.")
        return

    if src.count(INCLUDE_ANCHOR) != 1:
        fail(f"Expected exactly one occurrence of "
             f"`{INCLUDE_ANCHOR}` in legacy.py, found "
             f"{src.count(INCLUDE_ANCHOR)}. Aborting before any change.")

    # ---- parse + locate ----------------------------------------------
    tree = ast.parse(src)
    moved, router_targets = collect_moved(tree)
    if "packages_router" not in router_targets or \
            "runs_router" not in router_targets:
        fail("Could not find both `packages_router` and `runs_router` "
             "assignments at module level. Aborting.")
    endpoints = [n for n in moved
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if not endpoints:
        fail("Found the routers but no decorated endpoints — refusing to "
             "move an empty router layer. Aborting.")

    moved_names = set(router_targets) | {n.name for n in endpoints}

    # ---- free-name analysis ------------------------------------------
    free = set()
    for n in moved:
        free |= free_names_of(n)
    free -= moved_names
    free -= HEADER_LIB_NAMES
    free -= PY_BUILTINS

    # ---- introspect live app namespace (in the venv) ------------------
    intro = run(
        [VENV_PY, "-c",
         "import json,app; print(json.dumps(sorted(dir(app))))"],
        cwd=BACKEND)
    if intro.returncode != 0:
        fail("`import app` failed in the venv BEFORE any change was made — "
             "the repo is not in a clean state. Fix that first.\n"
             + intro.stderr)
    try:
        app_names = set(json.loads(intro.stdout.strip().splitlines()[-1]))
    except Exception as e:  # noqa: BLE001
        fail(f"Could not parse `dir(app)` from the venv: {e}\n{intro.stdout}")

    from_legacy = sorted(n for n in free if n in app_names)
    unresolved = sorted(n for n in free if n not in app_names)
    if unresolved:
        fail("These free names used by the moved code resolve to neither a "
             "library, a builtin, nor a name on `app`:\n  "
             + ", ".join(unresolved)
             + "\nThat usually means the move set is wrong for THIS file. "
               "Nothing was written.")

    # ---- build the new module text -----------------------------------
    offs = line_offsets(src)
    spans = sorted((node_span(offs, n) for n in moved), key=lambda s: s[0])
    lifted = "\n\n".join(src[a:b].rstrip("\n") for a, b in spans) + "\n"

    # Split the legacy dependencies into (a) names safe to bind by value at
    # import time — models, schemas, enums, constants, plain helpers — and
    # (b) test SEAMS that must be resolved through the legacy module at call
    # time so a monkeypatched stub is honored (see SEAM_NAMES above).
    seam_used = [n for n in from_legacy if n in SEAM_NAMES]
    static_from_legacy = [n for n in from_legacy if n not in SEAM_NAMES]

    if static_from_legacy:
        legacy_import_block = (
            "# Names that stay in legacy.py (models, schemas, helpers) but are\n"
            "# referenced by the moved router layer. Imported from the\n"
            "# partially-loaded legacy module — every name below is defined\n"
            "# earlier in legacy than the line that imports this module.\n"
            "from app.legacy import (  # noqa: E402,F401\n"
            + "".join(f"    {n},\n" for n in static_from_legacy)
            + ")\n"
        )
    else:
        legacy_import_block = ""

    if seam_used:
        seam_block = (
            "\n"
            "# ── Test seams ──────────────────────────────────────────────\n"
            "# These names are monkeypatched by the test-suite on the `app`\n"
            "# proxy, which forwards writes to app.legacy. We import the\n"
            "# legacy MODULE (not the names) and delegate on every call, so a\n"
            "# stub injected by a test is always seen here. Binding these by\n"
            "# value with `from app.legacy import ...` would snapshot the\n"
            "# original and silently defeat the seam.\n"
            "import app.legacy as _aptiro_legacy  # noqa: E402\n\n"
            + "\n\n".join(
                f"def {n}(*args, **kwargs):  # noqa: E302\n"
                f"    return _aptiro_legacy.{n}(*args, **kwargs)"
                for n in seam_used
            )
            + "\n"
        )
    else:
        seam_block = ""

    module_text = (
        MODULE_HEADER.format(marker=MARKER)
        + legacy_import_block
        + seam_block
        + "\n\n"
        + lifted
    )

    # ---- build the patched legacy text -------------------------------
    # Remove moved spans (highest offset first to keep indices valid).
    new_src = src
    for a, b in sorted(spans, key=lambda s: s[0], reverse=True):
        new_src = new_src[:a] + new_src[b:]

    # Insert the re-import on the line directly above the include anchor.
    # Re-import EVERY moved name (both routers AND every endpoint) so that
    # `import app as A; A.<name>` keeps resolving exactly as before the move
    # — this is the same back-compat contract PR-10 and PR-11 established.
    reimport_names = sorted(moved_names)
    inject = (
        f"# {MARKER}: packages/runs HTTP layer now lives in "
        f"app.modules.packages; re-exported here for back-compat.\n"
        "from app.modules.packages import (  # noqa: E402,F401\n"
        + "".join(f"    {n},\n" for n in reimport_names)
        + ")\n"
    )
    idx = new_src.index(INCLUDE_ANCHOR)
    line_start = new_src.rfind("\n", 0, idx) + 1
    new_src = new_src[:line_start] + inject + new_src[line_start:]

    # ---- report -------------------------------------------------------
    info(f"Routers moved      : packages_router, runs_router")
    info(f"Endpoints moved    : {len(endpoints)}  "
         f"({', '.join(sorted(e.name for e in endpoints))})")
    info(f"Imported from legacy ({len(static_from_legacy)}): "
         f"{', '.join(static_from_legacy)}")
    if seam_used:
        info(f"Call-time seams ({len(seam_used)}): "
             f"{', '.join(seam_used)} "
             f"(delegated to app.legacy so test stubs are honored)")
    info(f"New module         : {MOD_FILE.relative_to(ROOT)}")
    info(f"Re-exported to app : {len(moved_names)} names "
         f"(both routers + every endpoint), above `{INCLUDE_ANCHOR}`")

    if args.dry_run:
        info("DRY RUN — no files written. Re-run without --dry-run to apply.")
        return

    # ---- write to temp, byte-compile, then commit + validate ----------
    backup = src  # exact prior bytes, kept in memory for restore
    MOD_DIR.mkdir(parents=True, exist_ok=True)

    # syntax check both before touching the working tree
    for label, text in (("module", module_text), ("legacy", new_src)):
        with tempfile.NamedTemporaryFile(
                "w", suffix=".py", delete=False) as tf:
            tf.write(text)
            tmp = tf.name
        try:
            py_compile.compile(tmp, doraise=True)
        except py_compile.PyCompileError as e:
            pathlib.Path(tmp).unlink(missing_ok=True)
            fail(f"Generated {label} does not byte-compile:\n{e}")
        pathlib.Path(tmp).unlink(missing_ok=True)

    MOD_FILE.write_text(module_text)
    LEGACY.write_text(new_src)
    info("Wrote module + patched legacy.py. Validating…")

    def restore(reason):
        LEGACY.write_text(backup)
        _remove_module_tree()
        fail("VALIDATION FAILED — restored legacy.py and removed the new "
             f"module. Nothing changed.\n{reason}")

    imp = run([VENV_PY, "-c", "import app"], cwd=BACKEND)
    if imp.returncode != 0:
        restore("`import app` failed after patch:\n" + imp.stderr)

    pt = run([VENV_PY, "-m", "pytest", "-q"], cwd=BACKEND)
    tail = (pt.stdout or "")[-2500:] + (pt.stderr or "")[-1500:]
    if pt.returncode != 0:
        restore("pytest failed after patch:\n" + tail)

    info("pytest green after patch:\n" + tail.strip().splitlines()[-1]
         if tail.strip() else "pytest green.")
    info("PR-12 applied successfully.")
    print("\nNext:\n"
          "  cd %s\n"
          "  git add backend/app/modules/packages/__init__.py "
          "backend/app/legacy.py phase9_pr12_packages.py\n"
          "  git commit -m \"Phase 9 PR-12: extract modules/packages "
          "(packages + runs HTTP layer)\"\n"
          "  git push origin main\n" % ROOT)


if __name__ == "__main__":
    main()
