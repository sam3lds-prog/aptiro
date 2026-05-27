#!/usr/bin/env python3
"""One-shot patch: fix Phase 8 f-string syntax for Python 3.11 strict parsing.

Python 3.11 rejects whitespace around the !r conversion specifier inside an
f-string brace (`{ var !r }`). Python 3.12 accepts it. This script rewrites
the three offending Phase 8 lines so they parse on 3.11.

Run from the project root (the directory that contains `backend/`).
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
APP = ROOT / "backend" / "app.py"
if not APP.exists():
    # also try relative cwd
    APP = pathlib.Path("backend/app.py").resolve()
if not APP.exists():
    print(f"ERROR: backend/app.py not found from {pathlib.Path.cwd()}", file=sys.stderr)
    sys.exit(1)

src = APP.read_text()
original = src

# The three actual broken lines (whitespace around !r is illegal in 3.11):
fixes = [
    (
        'f"[aptiro] p8 col { tname }.{ colname } skipped: { _ce8!r }"',
        'f"[aptiro] p8 col {tname}.{colname} skipped: {_ce8!r}"',
    ),
    (
        'f"[aptiro] p8_ensure_columns skipped: { _e8!r }"',
        'f"[aptiro] p8_ensure_columns skipped: {_e8!r}"',
    ),
    (
        'f"[aptiro] p8 set_expiry failed: { _e8s!r }"',
        'f"[aptiro] p8 set_expiry failed: {_e8s!r}"',
    ),
    # Defensive cleanup of other Phase 8 f-strings with stray spaces (these
    # parse fine on 3.11 but cleaner without spaces):
    ('f"auth:{ ip }"', 'f"auth:{ip}"'),
    ('f"mut:{ ip }"', 'f"mut:{ip}"'),
    (
        'f\'ALTER TABLE "{ tname }" ADD COLUMN "{ colname }" { coldecl }\'',
        'f\'ALTER TABLE "{tname}" ADD COLUMN "{colname}" {coldecl}\'',
    ),
    (
        "f\"{ pkg.company or 'aptiro' }_{ et.artifact }\"",
        "f\"{pkg.company or 'aptiro'}_{et.artifact}\"",
    ),
    (
        "f\"{ safe or 'aptiro_export' }.{ ext }\"",
        "f\"{safe or 'aptiro_export'}.{ext}\"",
    ),
    (
        'f\'attachment; filename="{ fname }"\'',
        'f\'attachment; filename="{fname}"\'',
    ),
]

count = 0
for old, new in fixes:
    if old in src:
        src = src.replace(old, new)
        count += 1
        print(f"  fixed: {old[:70]}{'...' if len(old) > 70 else ''}")

if src != original:
    APP.write_text(src)
    print(f"\nPatched {count} f-string(s) in {APP}")
else:
    print(f"\nNo changes needed (already patched). Checked {APP}")

# Compile-check
import py_compile
try:
    py_compile.compile(str(APP), doraise=True)
    print("\nSyntax OK — backend/app.py compiles cleanly on this Python.")
except py_compile.PyCompileError as e:
    print(f"\nFAIL — app.py still has a syntax error:\n{e}", file=sys.stderr)
    sys.exit(2)

print("\nNow run: cd backend && pytest -q")
