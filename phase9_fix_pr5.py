#!/usr/bin/env python3
"""
Aptiro — Phase 9 PR-5 fix.

Root cause:
  The original PR-5 script used a single broad regex:
    r"_IS_SQLITE = DATABASE_URL\\.startswith.*?engine = create_engine(DATABASE_URL[^)]+)\\n"
  with re.DOTALL. In the actual legacy.py _IS_SQLITE sits at ~line 96 and
  engine = create_engine(...) at ~line 500, with all enum/model definitions
  in between. The non-greedy .*? still had to cross 400 lines to reach the
  endpoint, deleting SourceType and every other class along the way.

Fix:
  1. Restore backend/app/legacy.py from the commit before PR-5 (HEAD~1).
  2. Re-apply PR-5 using FIVE tight, individual patterns — one per name —
     none of which span more than 2-3 lines:
       PAT_ISSQLITE    → _IS_SQLITE = DATABASE_URL.startswith(...)
       PAT_NOW         → def _now(): return datetime.now(...)
       PAT_CONNECT     → _connect_args = {...} if _IS_SQLITE else {}
       PAT_ENGINE_DECL → engine = create_engine(DATABASE_URL, ...)
       PAT_SESSION     → def get_session(): with Session(engine) as s: yield s
  3. Verify all structural checks pass.
  4. Print instructions to run pytest and amend/push.

Run from the project root (the directory containing backend/).
"""
import pathlib
import py_compile
import re
import subprocess
import sys

ROOT    = pathlib.Path.cwd()
BACKEND = ROOT / "backend"
LEGACY  = BACKEND / "app" / "legacy.py"
ENG_FILE = BACKEND / "app" / "db" / "engine.py"
MARKER  = "APTIRO_PHASE9_PR5_DB_MARKER"

def fail(msg, code=1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)

if not BACKEND.is_dir():
    fail("backend/ not found. Run from the project root.")

# ---------------------------------------------------------------------------
# Step 1: Restore legacy.py from the commit before PR-5 (HEAD~1)
# ---------------------------------------------------------------------------
print("Step 1/4  Restoring legacy.py from HEAD~1 (pre-PR5 state)...")

result = subprocess.run(
    ["git", "show", "HEAD~1:backend/app/legacy.py"],
    capture_output=True, text=True, cwd=ROOT
)
if result.returncode != 0:
    fail(f"git show HEAD~1 failed:\n{result.stderr}")

pre_pr5 = result.stdout
if "_IS_SQLITE = DATABASE_URL" not in pre_pr5:
    fail("Restored legacy.py does not contain '_IS_SQLITE = DATABASE_URL'. "
         "HEAD~1 may not be the correct pre-PR5 commit.")

LEGACY.write_text(pre_pr5)
nlines = pre_pr5.count("\n") + 1
print(f"  ✓ restored {len(pre_pr5):,} chars (~{nlines:,} lines)")

legacy_src = pre_pr5

# ---------------------------------------------------------------------------
# Step 2: Five tight individual patterns
# ---------------------------------------------------------------------------
print("\nStep 2/4  Locating patterns...")

# Pattern A: _IS_SQLITE = DATABASE_URL.startswith(...) — single line
PAT_ISSQLITE = re.compile(
    r"_IS_SQLITE = DATABASE_URL\.startswith[^\n]+\n"
)

# Pattern B: def _now(): return datetime.now(timezone.utc) — 2 lines
PAT_NOW = re.compile(
    r"def _now\(\):\s*\n[ \t]+return datetime\.now\(timezone\.utc\)[ \t]*\n",
    re.DOTALL,
)

# Pattern C: _connect_args = {...} if _IS_SQLITE else {} — single line
PAT_CONNECT = re.compile(
    r"_connect_args = \{[^\n]*\}[^\n]*\n"
)

# Pattern D: engine = create_engine(DATABASE_URL, ...) — 1 or 2 lines
# Anchored: must contain DATABASE_URL and connect_args=_connect_args
PAT_ENGINE_DECL = re.compile(
    r"engine = create_engine\(DATABASE_URL[^)]*connect_args=_connect_args\)[ \t]*\n",
    re.DOTALL,
)

# Pattern E: def get_session(): with Session(engine) as s: yield s — 3 lines
PAT_SESSION = re.compile(
    r"def get_session\(\):\s*\n[ \t]+with Session\(engine\) as s:\s*\n[ \t]+yield s[ \t]*\n",
    re.DOTALL,
)

patterns = [
    ("_IS_SQLITE",    PAT_ISSQLITE),
    ("_now()",        PAT_NOW),
    ("_connect_args", PAT_CONNECT),
    ("engine decl",   PAT_ENGINE_DECL),
    ("get_session",   PAT_SESSION),
]

matches = {}
for name, pat in patterns:
    m = pat.search(legacy_src)
    if not m:
        fail(f"Pattern for '{name}' not found in legacy.py.\n"
             f"The file structure may differ from what was expected.\n"
             f"To inspect: grep -n '{name}' backend/app/legacy.py")
    matches[name] = m
    line = legacy_src[:m.start()].count("\n") + 1
    print(f"  ✓ {name:<16} line ~{line:>4}  ({len(m.group(0)):>4} chars)")

# Sanity: confirm none of the individual matches are unreasonably large
for name, m in matches.items():
    if len(m.group(0)) > 500:
        fail(f"Pattern '{name}' matched {len(m.group(0))} chars — suspiciously large.\n"
             f"The pattern may be too broad. Matched text:\n{m.group(0)[:200]}")

# Confirm order: _IS_SQLITE before _now() before _connect_args before engine before get_session
order = ["_IS_SQLITE", "_now()", "_connect_args", "engine decl", "get_session"]
positions = {n: matches[n].start() for n in order}
for i in range(len(order) - 1):
    a, b = order[i], order[i+1]
    if positions[a] >= positions[b]:
        fail(f"Unexpected order: '{a}' (pos {positions[a]}) comes after '{b}' "
             f"(pos {positions[b]}). legacy.py structure differs from expected.")
print("  ✓ order confirmed: " + " < ".join(order))

# ---------------------------------------------------------------------------
# Build the combined import block (goes where _IS_SQLITE was removed)
# ---------------------------------------------------------------------------
IMPORT_BLOCK = (
    "# " + MARKER + "\n"
    "# _now, _IS_SQLITE, engine, get_session extracted to db/engine.py (Phase 9 PR-5)\n"
    "from app.db.engine import (  # noqa: F401\n"
    "    _now, _IS_SQLITE, engine, get_session,\n"
    ")\n"
)

# ---------------------------------------------------------------------------
# Step 3: Apply replacements back-to-front to preserve offsets
# ---------------------------------------------------------------------------
print("\nStep 3/4  Applying replacements (back-to-front)...")

# _IS_SQLITE → IMPORT_BLOCK, all others → one-line comment
replacements = [
    (matches["get_session"].start(),    matches["get_session"].end(),
     "# get_session imported from app.db.engine (Phase 9 PR-5)\n", "get_session"),
    (matches["engine decl"].start(),    matches["engine decl"].end(),
     "# engine imported from app.db.engine (Phase 9 PR-5)\n", "engine decl"),
    (matches["_connect_args"].start(),  matches["_connect_args"].end(),
     "# _connect_args defined in app.db.engine (Phase 9 PR-5)\n", "_connect_args"),
    (matches["_now()"].start(),         matches["_now()"].end(),
     "# _now imported from app.db.engine (Phase 9 PR-5)\n", "_now()"),
    (matches["_IS_SQLITE"].start(),     matches["_IS_SQLITE"].end(),
     IMPORT_BLOCK, "_IS_SQLITE + import block"),
]
# Sort descending (back-to-front)
replacements.sort(key=lambda t: t[0], reverse=True)

patched = legacy_src
for start, end, repl, label in replacements:
    patched = patched[:start] + repl + patched[end:]
    print(f"  ✓ {label:<28} ({end-start:>5} → {len(repl):>3} chars)")

LEGACY.write_text(patched)

# ---------------------------------------------------------------------------
# Byte-compile
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
    fail("Compile failed. Check the output above.\n"
         "The restored pre-PR5 legacy.py is intact; only the re-application failed.", code=4)

# ---------------------------------------------------------------------------
# Step 4: Structural checks
# ---------------------------------------------------------------------------
print("\nStep 4/4  Structural checks...")
final = LEGACY.read_text()

checks = [
    ("MARKER present",              MARKER in final),
    ("from app.db.engine import",   "from app.db.engine import" in final),
    ("_now in import",              "_now," in final and "app.db.engine" in final),
    ("engine in import",            "engine," in final and "app.db.engine" in final),
    ("get_session in import",       "get_session," in final and "app.db.engine" in final),
    ("no _IS_SQLITE = assignment",  "_IS_SQLITE = DATABASE_URL" not in final),
    ("no engine = create_engine",   "engine = create_engine(" not in final),
    ("no _connect_args = assign",   "_connect_args = {" not in final),
    ("no def _now",                 "def _now():" not in final),
    ("no def get_session",          "def get_session():" not in final),
    ("SourceType still there",      "class SourceType" in final),
    ("Source model still there",    "class Source(" in final),
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
    fail("One or more structural checks failed (see ✗ above).", code=5)

print(f"""
═══════════════════════════════════════════════════════════════════════
Phase 9 PR-5 fix applied.

legacy.py has been restored from HEAD~1 and re-patched with 5 tight
individual patterns. SourceType, Source, and all other models are intact.

Next:
  cd backend
  . .venv/bin/activate
  pytest -q                   ← must be 218 passed

Then amend the PR-5 commit and force-push:
  cd ..
  git add backend/app/legacy.py
  git add phase9_fix_pr5.py
  git commit --amend --no-edit
  git push origin main --force-with-lease
═══════════════════════════════════════════════════════════════════════
""")
