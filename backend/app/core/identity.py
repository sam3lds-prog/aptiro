"""
Aptiro backend — core/identity.py (Phase 9 PR-4).

Per-request identity: the ContextVar holding the current user ID and
the accessor function.

Extracted from backend/app/legacy.py.

Self-contained: depends only on stdlib + app.core.config.DEFAULT_UID.

Public names (re-exported by legacy.py so `import app as A; A.X` works):
    _CURRENT_UID   ContextVar[str]  — set by auth middleware at request start
    _uid()         -> str           — read the current request's user ID

The auth middleware in legacy.py calls:
    utok = _CURRENT_UID.set(uid)     # set for this request
    ...
    _CURRENT_UID.reset(utok)         # restore previous value

These use the same ContextVar object exported here, so the context
isolation is preserved exactly as before.
"""
from contextvars import ContextVar

from app.core.config import DEFAULT_UID

# Per-request current user id.
# Defaults to DEFAULT_UID ("local") so code paths with no auth context
# (tests, single-user mode) work identically to pre-Phase-4 behaviour.
_CURRENT_UID: ContextVar[str] = ContextVar("aptiro_uid", default=DEFAULT_UID)


def _uid() -> str:
    """Return the current request's user ID.

    Returns DEFAULT_UID ("local") when called outside a request context,
    e.g. in tests, startup code, or single-user mode.
    """
    return _CURRENT_UID.get()
