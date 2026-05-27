"""
Aptiro backend — core/config.py (Phase 9 PR-3).

All environment-variable-driven configuration, startup flags, URL-fetch
limits, ConfigError, and validate_config().

Extracted from backend/app/legacy.py.
Self-contained: depends only on stdlib + app.core.observability._logj.

Public names (all re-exported by legacy.py):
    _DEFAULT_DATABASE_URL, DATABASE_URL
    AI_PROVIDER, EMBEDDING_PROVIDER, JOB_PROVIDER
    SEARCH_PROVIDER, NOTIFICATION_PROVIDER, SEED_ON_STARTUP
    AUTH_ENABLED, DEFAULT_UID, DEFAULT_USER_EMAIL, _PW_ROUNDS
    URL_FETCH_TIMEOUT, URL_FETCH_MAX_BYTES
    ConfigError, validate_config
"""
import importlib.util as _ilu
import os
import sys as _sys

from app.core.observability import _logj  # used in validate_config warning

# ---------------------------------------------------------------------------
# Database URL
# ---------------------------------------------------------------------------
_DEFAULT_DATABASE_URL = "sqlite:///./aptiro.db"

# Every Aptiro setting is read from an APTIRO_-prefixed environment
# variable so it never collides with generic vars other apps may use.


def _resolve_database_url():
    url = os.getenv("APTIRO_DATABASE_URL") or _DEFAULT_DATABASE_URL
    if url.startswith("sqlite"):
        return url
    if url.startswith(("postgresql", "postgres")):
        if not (_ilu.find_spec("psycopg2") or _ilu.find_spec("psycopg")):
            print(
                f"[aptiro] APTIRO_DATABASE_URL={url!r} needs a Postgres "
                f"driver that is not installed; falling back to "
                f"{_DEFAULT_DATABASE_URL}. `pip install psycopg2-binary` "
                f"for Postgres.",
                file=_sys.stderr,
            )
            return _DEFAULT_DATABASE_URL
    return url


DATABASE_URL = _resolve_database_url()

# ---------------------------------------------------------------------------
# Provider selection (all overridable via APTIRO_*_PROVIDER env vars)
# ---------------------------------------------------------------------------
AI_PROVIDER           = os.getenv("APTIRO_AI_PROVIDER",           "mock")
EMBEDDING_PROVIDER    = os.getenv("APTIRO_EMBEDDING_PROVIDER",    "mock")
JOB_PROVIDER          = os.getenv("APTIRO_JOB_PROVIDER",          "mock")
SEARCH_PROVIDER       = os.getenv("APTIRO_SEARCH_PROVIDER",       "mock")
NOTIFICATION_PROVIDER = os.getenv("APTIRO_NOTIFICATION_PROVIDER", "mock")
SEED_ON_STARTUP       = os.getenv("APTIRO_SEED_ON_STARTUP", "1") == "1"

# ---------------------------------------------------------------------------
# Auth constants (runtime identity state — _CURRENT_UID, _uid — lives in
# legacy.py and will move to core/identity.py in PR-4)
# ---------------------------------------------------------------------------
# AUTH defaults to OFF. With AUTH off every request runs as the single
# built-in "local" user so the entire prior test suite and existing
# single-user data behave EXACTLY as before.
AUTH_ENABLED      = os.getenv("APTIRO_AUTH", "off").lower() in (
    "on", "1", "true")
DEFAULT_UID       = "local"
DEFAULT_USER_EMAIL = "local@aptiro.local"
_PW_ROUNDS        = 120_000

# ---------------------------------------------------------------------------
# URL-fetch safety limits
# ---------------------------------------------------------------------------
# Server-side fetch of a user-supplied PUBLIC URL only — never a crawler.
URL_FETCH_TIMEOUT  = float(os.getenv("APTIRO_URL_FETCH_TIMEOUT",   "10"))
URL_FETCH_MAX_BYTES = int(
    os.getenv("APTIRO_URL_FETCH_MAX_BYTES", str(2 * 1024 * 1024)))

# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

class ConfigError(RuntimeError):
    pass


def validate_config():
    """Fail fast on genuinely invalid configuration with a clear message.

    Defaults are always valid, so normal startup and the test suite are
    unaffected; only explicitly bad env values trip this.
    """
    problems = []
    if not (DATABASE_URL or "").strip():
        problems.append("APTIRO_DATABASE_URL is empty")
    a = os.getenv("APTIRO_AUTH", "off").lower()
    if a not in ("on", "off", "1", "0", "true", "false"):
        problems.append("APTIRO_AUTH must be on/off (got %r)" % a)
    for name, raw in (
            ("APTIRO_URL_FETCH_TIMEOUT",
             os.getenv("APTIRO_URL_FETCH_TIMEOUT")),
            ("APTIRO_URL_FETCH_MAX_BYTES",
             os.getenv("APTIRO_URL_FETCH_MAX_BYTES")),
            ("APTIRO_AI_MAX_TOKENS", os.getenv("APTIRO_AI_MAX_TOKENS")),
            ("APTIRO_AI_TIMEOUT",    os.getenv("APTIRO_AI_TIMEOUT"))):
        if raw is None:
            continue
        try:
            if float(raw) <= 0:
                problems.append(
                    "%s must be > 0 (got %r)" % (name, raw))
        except (TypeError, ValueError):
            problems.append(
                "%s must be numeric (got %r)" % (name, raw))
    if (os.getenv("APTIRO_AI_PROVIDER", "mock").lower() == "anthropic"
            and not os.getenv("ANTHROPIC_API_KEY")):
        # Not fatal: provider falls back to mock, but make it loud.
        _logj("config.warning",
              message="APTIRO_AI_PROVIDER=anthropic but ANTHROPIC_API_"
                      "KEY is unset; falling back to the mock provider")
    if problems:
        raise ConfigError(
            "Invalid configuration: " + "; ".join(problems))
    return True
