#!/usr/bin/env python3
"""Fix Phase 8: the existing app.py imports `os` (no underscore alias), but
the Phase 8 block I appended uses `_os.getenv(...)`. This script rewrites
the Phase 8 block to use the correct `os.` name.

Idempotent — safe to re-run.
Run from the project root (the directory containing `backend/`).
"""
import pathlib
import py_compile
import sys

APP = pathlib.Path("backend/app.py").resolve()
if not APP.exists():
    print(f"ERROR: backend/app.py not found from {pathlib.Path.cwd()}", file=sys.stderr)
    sys.exit(1)

src = APP.read_text()
marker = "APTIRO_PHASE8_AUTH_HARDENING_MARKER"
idx = src.find(marker)
if idx < 0:
    print(f"ERROR: {marker} not found — Phase 8 block missing?", file=sys.stderr)
    sys.exit(1)

before = src[:idx]
after = src[idx:]
hits = after.count("_os.")
after_fixed = after.replace("_os.", "os.")

if hits:
    APP.write_text(before + after_fixed)
    print(f"Patched {hits} occurrence(s) of `_os.` -> `os.` in the Phase 8 block.")
else:
    print("No changes (already patched).")

# Verify everything else parses too
try:
    py_compile.compile(str(APP), doraise=True)
    print("\nSyntax OK — backend/app.py compiles cleanly.")
except py_compile.PyCompileError as e:
    print(f"\nSTILL FAILING:\n{e}", file=sys.stderr)
    sys.exit(2)

print("\nNow run: pytest -q  (from the backend directory)")
