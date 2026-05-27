# Phase 9 — Next Steps: PR-2 through PR-N

PR-1 scaffold is complete. `backend/app/legacy.py` holds all the code.
`backend/app/__init__.py` re-exports everything so `import app as A; A.X`
works for any caller. The scaffold directories are empty placeholders.

This document is your playbook for subsequent PRs. One PR per chat session
per the roadmap rule. Tests must be green before AND after each move.

---

## The golden rule (repeat it every PR)

> One PR = one moved module. `pytest -q` green before. Move the module.
> `pytest -q` green after. Commit. Only then start the next module.

---

## Recommended order (lowest risk → highest)

| PR  | Module               | What moves                                                    | Deps inside legacy |
|-----|----------------------|---------------------------------------------------------------|--------------------|
| 2   | `core/observability` | `_log`, `_logj`, `_REQ_ID` (ContextVar)                      | none               |
| 3   | `core/config`        | `validate_config`, `AUTH_ENABLED`, `SEED_ON_STARTUP`, env vars| `_logj` → PR-2    |
| 4   | `core/identity`      | `_uid`, `DEFAULT_UID`, `DEFAULT_USER_EMAIL`, `_current_uid`  | `AUTH_ENABLED` → PR-3 |
| 5   | `db/engine`          | `engine`, `get_session`, `_ensure_additive_columns`, `_now`  | `_resolve_database_url` |
| 6   | `modules/auth`       | `User`, `_hash_pw`, `_verify_pw`, `_new_token`, `auth_router`| `get_session`, `_uid` |
| 7   | `modules/sources`    | `Source`, `SourceRef`, sources router, ingestion bindings     | `get_session`, models |
| 8   | `modules/profile_truth` | `ProfileClaim`, `extract_claims`, `parse_document`        | `Source`, `SourceRef` |
| 9   | `modules/jobs`       | `JobPosting`, `import_job`, job router, providers             | full model set     |
| 10  | `modules/strategies` | `Strategy`, strategies router, presets                        | `score_job` (PR-11)|
| 11  | `modules/scoring`    | `score_job`, `_structured_requirements`                       | `Strategy`, `JobPosting` |
| 12  | `modules/packages`   | `ApplicationPackage`, `PackageBullet`, packages router, export| all of the above  |
| 13  | `modules/research`   | `PublicResearchFinding`, research router                      | `ProfileClaim`     |
| 14  | `modules/applications`| `Application`, applications router, state machine            | `ApplicationPackage` |
| 15  | `modules/notifications`| `NotifPref`, `InAppNotification`, notif routers, SMTP       | all of the above  |
| 16  | `legacy.py` cleanup` | Remaining glue in legacy.py moved to `main.py`               | everything         |

---

## Template for each PR

When you start a new chat for e.g. PR-2, send this prompt to Claude:

```
Phase 9 PR-2: extract `core/observability.py` from `backend/app/legacy.py`.

Rules:
- Pure refactor. Zero behavior change. Tests green before and after.
- After the move, `legacy.py` imports the names back from the new module
  so its own internal references stay valid.
- `backend/app/__init__.py` does NOT need changes (it re-exports from
  legacy dynamically; legacy now imports from core.observability, so
  those names flow through).

Names to move to `backend/app/core/observability.py`:
  _log         (the logging.Logger)
  _logj        (the structured JSON log function)
  _REQ_ID      (ContextVar[str])

After the move, at the top of legacy.py add:
  from app.core.observability import _log, _logj, _REQ_ID  # noqa: F401

Deliver:
  1. Full `backend/app/core/observability.py`
  2. The exact lines to ADD to the top of `backend/app/legacy.py`
     (as a deploy script that patches the file, per the usual pattern)
  3. Deploy commands
  4. `pytest -q` expected to pass with same count

Stop and report.
```

---

## How a module move works (concrete pattern)

**Before** (code is in legacy.py):
```python
# backend/app/legacy.py
import logging as _logging
_log = _logging.getLogger("aptiro")

def _logj(event: str, **kwargs):
    ...
```

**After** (code moves to its module, legacy re-imports):
```python
# backend/app/core/observability.py   ← NEW, contains the definition
import logging as _logging
_log = _logging.getLogger("aptiro")
def _logj(event: str, **kwargs):
    ...

# backend/app/legacy.py   ← CHANGE: remove definition, add import
from app.core.observability import _log, _logj, _REQ_ID  # noqa: F401
# (everything else in legacy.py is unchanged)
```

**`backend/app/__init__.py`** — NO CHANGES. Because `legacy` now imports
`_log, _logj, _REQ_ID` from `core.observability`, those names still
appear in `dir(legacy)` and flow through to the package namespace.

Tests continue doing `import app as A; A._logj(...)` with no changes.

---

## What `main.py` looks like at the end (PR-16)

After all modules are extracted, `legacy.py` shrinks to near-nothing and
gets renamed `main.py`. It contains only:

```python
# backend/app/main.py
"""Aptiro — application entry point. All code lives in modules/."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import validate_config, SEED_ON_STARTUP
from app.core.observability import _logj
from app.db.engine import init_db
from app.modules.sources.router import sources_router
from app.modules.profile_truth.router import claims_router
# ... all other router imports ...

@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_config()
    _logj("startup")
    init_db()
    if SEED_ON_STARTUP:
        ...
    yield
    _logj("shutdown")

app = FastAPI(title="Aptiro API", version="0.9.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, ...)

# Auth + observability middleware
@app.middleware("http")
async def _auth_context(request, call_next): ...

# Register all routers
app.include_router(sources_router)
app.include_router(claims_router)
# ...
```

And `__init__.py` can be simplified to just:
```python
from .main import app  # noqa: F401
# explicit re-exports for anything tests use off `A.*`
```

---

## Deferred modules (from ROADMAP.md)

> If a split risks breaking tests, defer that module.

Candidates to defer:
- The scoring engine (`score_job`) — it has many internal cross-references;
  move it only after the models it depends on are extracted.
- The export logic (`exporting.py` is already separate; the in-app export
  router can follow once `packages` is extracted).

---

## Tracking progress

Check off each PR here as it's completed and committed:

- [ ] PR-1  Scaffold (this PR)
- [ ] PR-2  `core/observability`
- [ ] PR-3  `core/config`
- [ ] PR-4  `core/identity`
- [ ] PR-5  `db/engine`
- [ ] PR-6  `modules/auth`
- [ ] PR-7  `modules/sources`
- [ ] PR-8  `modules/profile_truth`
- [ ] PR-9  `modules/jobs`
- [ ] PR-10 `modules/strategies`
- [ ] PR-11 `modules/scoring`
- [ ] PR-12 `modules/packages`
- [ ] PR-13 `modules/research`
- [ ] PR-14 `modules/applications`
- [ ] PR-15 `modules/notifications`
- [ ] PR-16 `legacy.py` → `main.py` cleanup
