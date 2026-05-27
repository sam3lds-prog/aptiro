"""
Aptiro backend — core/observability.py (Phase 9 PR-2).

Structured JSON logging and per-request correlation ID.
Extracted from backend/app/legacy.py (the Phase 6 observability block).

Self-contained: no imports from other app.* modules.

Public names (all re-exported by legacy.py so `import app as A; A.X`
keeps working):
    _REQUEST_ID   ContextVar[str]  — set by auth middleware at request start
    _rid()        -> str           — read the current request's ID
    _log          logging.Logger   — the singleton "aptiro" logger
    _logj()       -> None          — emit one structured JSON line
"""
import json as _json
import logging as _logging
import os
import sys as _sys
from contextvars import ContextVar
from datetime import datetime, timezone


# Per-request correlation ID.
# The auth middleware in legacy.py calls _REQUEST_ID.set(rid) at the start
# of every request. Default "-" is used outside of request context (startup,
# shutdown, seed, unit tests that call _logj directly).
_REQUEST_ID: ContextVar[str] = ContextVar("aptiro_rid", default="-")


def _rid() -> str:
    """Return the active request's correlation ID (or "-" outside a request)."""
    return _REQUEST_ID.get()


# The single shared "aptiro" logger.
# Handler and level are configured once at module import time. APTIRO_LOG_LEVEL
# controls the level (default INFO).
_log = _logging.getLogger("aptiro")
if not _log.handlers:
    _h = _logging.StreamHandler(_sys.stdout)
    _h.setFormatter(_logging.Formatter("%(message)s"))
    _log.addHandler(_h)
    _log.setLevel(os.getenv("APTIRO_LOG_LEVEL", "INFO").upper())
    _log.propagate = False


def _logj(event: str, **fields) -> None:
    """Emit one structured JSON log line.

    Always includes: ts (ISO-8601 UTC), event, request_id.
    Additional keyword arguments are merged in.
    Never raises — log failures are silently swallowed to avoid masking
    application errors.
    """
    try:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "request_id": _rid(),
        }
        rec.update(fields)
        _log.info(_json.dumps(rec, default=str))
    except Exception:
        pass
