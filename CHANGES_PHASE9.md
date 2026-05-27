# CHANGES — Phase 9: Backend modularization (PR-1 scaffold)

Phase 9 is a **pure refactor** — zero behaviour change, zero new features.
The full test suite (all prior tests) passes identically before and after
every PR. This document covers PR-1 (the scaffold).

---

## What changed

### `backend/app.py` → `backend/app/legacy.py`

The monolithic `backend/app.py` is moved verbatim into a new Python
package as `backend/app/legacy.py`. Every line of code is preserved
exactly, including all routes, models, helpers, and private names.

### `backend/app/__init__.py` (NEW)

The package entry point is a **dynamic re-export bridge**:

```python
from . import legacy as _legacy

_PKG_DUNDERS = frozenset({...})   # exclude Python's own dunder attrs

for _name in dir(_legacy):
    if _name in _PKG_DUNDERS:
        continue
    globals()[_name] = getattr(_legacy, _name)
```

This copies every top-level name from `legacy.py` into the package
namespace — public names (`Source`, `JobPosting`, `app`, `engine`, …)
AND private helpers the test suite reaches for (`_uid`, `_logj`,
`_REQ_ID`, `_mw_session`, `_classify_header`, `_P8_RL`, …).

Result: `import app as A; A.X` works for every `X` that worked before.

### Scaffold directories (NEW, all empty `__init__.py` placeholders)

```
backend/app/
  core/                   ← observability, config, identity helpers
  db/                     ← engine, session, migrations
  modules/
    sources/              ← Source, SourceRef, ingestion router
    profile_truth/        ← ProfileClaim, extraction, provenance
    jobs/                 ← JobPosting, providers, job router
    strategies/           ← Strategy, strategies router, presets
    scoring/              ← score_job, structured requirements
    packages/             ← ApplicationPackage, bullets, export
    research/             ← PublicResearchFinding, research router
    applications/         ← Application, tracker, state machine
    notifications/        ← NotifPref, InAppNotification, send logic
    auth/                 ← User, auth router, rate-limiter
```

These directories are empty placeholders. Content moves in from
`legacy.py` one module per PR in subsequent phases.

---

## What did NOT change

- Every line of code in the existing `backend/app.py` is preserved verbatim
  in `legacy.py` — no logic altered, no routes changed, no model schema
  changed.
- No migration files added (no schema change).
- No test files modified.
- `uvicorn app:app` still resolves to the same FastAPI instance.
- `alembic/env.py`'s `import app` still works (package now, but the import
  path is identical and `SQLModel.metadata` is still populated by legacy.py).

---

## Validation

```bash
# Apply PR-1
cd /path/to/Aptiro
python3 phase9_modularize.py

# Confirm structure
ls backend/app/
ls backend/app/modules/

# Run full test suite (must equal prior count, all green)
cd backend
. .venv/bin/activate
pytest -q

# Confirm import contract
python3 -c "
import sys; sys.path.insert(0, '.')
import app as A
assert hasattr(A, 'app')          and callable(A.app.get)
assert hasattr(A, 'engine')
assert hasattr(A, 'Source')
assert hasattr(A, 'JobPosting')
assert hasattr(A, 'get_session')
assert hasattr(A, '_uid')
assert hasattr(A, '_logj')
assert hasattr(A, 'parse_document')
print('import contract OK — all expected names present')
"
```

---

## Rollback

```bash
cd /path/to/Aptiro
python3 phase9_modularize.py --revert
# Then:
cd backend && pytest -q    # should be identical to pre-PR-1
```

---

## Cross-cutting guardrails (unchanged)

- Mock providers stay the default; suite runs offline, no key.
- No auto-submit. No scraping. No fabricated claims.
- Export gate and grounding gate are load-bearing — both unchanged.
- `pytest -q` green; no tests removed or modified.
