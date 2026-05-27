#!/usr/bin/env python3
"""
Aptiro — Phase 9 init fix: replace the glob-copy __init__.py with a
transparent module proxy.

Root cause of the 12 test failures:
  The original Phase 9 __init__.py used a glob-copy:
    globals()[name] = getattr(_legacy, name)
  This creates a snapshot — a copy of each value at import time.
  When tests do `monkeypatch.setattr(A, "AUTH_ENABLED", True)`, they
  set `app.AUTH_ENABLED` (the copy), but code inside legacy.py still
  reads `AUTH_ENABLED` from its OWN module globals — the original value.
  The two namespaces are disconnected, so patches have no effect.

Fix — module proxy:
  Replace __init__.py with a custom types.ModuleType subclass whose
  __setattr__ forwards every write to legacy.py. Now:
    monkeypatch.setattr(A, "X", v)  →  setattr(proxy, "X", v)
                                     →  __setattr__ forwards to legacy
                                     →  setattr(legacy_mod, "X", v)
  Code inside legacy.py immediately sees the patched value. ✓

Run from the project root (the directory containing `backend/`).
Idempotent — safe to re-run.

Usage:
    python3 phase9_fix_init.py
"""
import pathlib
import py_compile
import sys


ROOT = pathlib.Path.cwd()
BACKEND = ROOT / "backend"
APP_PKG = BACKEND / "app"
INIT_FILE = APP_PKG / "__init__.py"
LEGACY_FILE = APP_PKG / "legacy.py"


def fail(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


if not BACKEND.is_dir():
    fail(f"{BACKEND} not found. Run from the project root.")
if not APP_PKG.is_dir() or not LEGACY_FILE.exists():
    fail(
        f"{APP_PKG} package or {LEGACY_FILE} not found.\n"
        f"Phase 9 PR-1 must be applied first:\n"
        f"  python3 phase9_modularize.py"
    )

# ---------------------------------------------------------------------------
# The correct __init__.py — module proxy that forwards both reads and writes
# to legacy.py. This is what makes monkeypatch.setattr(A, "X", v) work.
# ---------------------------------------------------------------------------
INIT_SRC = '''\
"""
Aptiro backend — modular package (Phase 9).

This __init__.py is a transparent MODULE PROXY to `legacy.py`.

Both attribute READS and WRITES are forwarded to `legacy.py`:

  import app as A

  A.X                # reads legacy.X via __getattr__
  A.X = v            # writes legacy.X via __setattr__
  monkeypatch.setattr(A, "X", v)   # same as A.X = v → legacy.X = v

The write-forwarding is critical: pytest's monkeypatch (and direct
assignment tests like `A.engine = test_engine`) must reach legacy.py's
namespace so that the code running inside legacy.py sees the patched
value immediately.

The earlier glob-copy approach (globals()[name] = getattr(_legacy, name))
only created a snapshot — writes to the app package namespace were not
visible to code inside legacy.py. This proxy fixes all 12 failures.

PR-2..PR-N will gradually move chunks of code out of legacy.py. As each
module is extracted, legacy.py imports its names back from the new home,
so they continue to appear in dir(legacy) and flow through this proxy.
"""
import sys
import types
from . import legacy as _legacy_mod

# Capture package metadata before we replace this module in sys.modules.
_PATH = list(__path__)
_FILE = __file__
_SPEC = __spec__
_PKG  = __package__
_LOADER = __loader__

# Names that belong to the proxy itself and must NOT be forwarded to
# legacy.py. Everything else — including private names tests reach for
# (_uid, _logj, _P8_RL, AUTH_ENABLED, etc.) — is forwarded.
_PROXY_OWN = frozenset({
    "_legacy",
    "__name__", "__doc__", "__package__", "__loader__",
    "__spec__", "__path__", "__file__", "__cached__",
    "__builtins__", "__dict__", "__class__", "__weakref__",
})


class _LegacyProxy(types.ModuleType):
    """Transparent proxy: reads and writes target backend/app/legacy.py.

    __getattr__ is only called when normal instance/class lookup fails.
    The proxy\'s own attrs (__name__, __path__, _legacy, etc.) are stored
    on the instance via object.__setattr__ so they are found first.
    Everything else is delegated to legacy.
    """

    def __getattr__(self, name):
        try:
            legacy = object.__getattribute__(self, "_legacy")
            return getattr(legacy, name)
        except AttributeError:
            mod_name = object.__getattribute__(self, "__name__")
            raise AttributeError(
                f"module {mod_name!r} has no attribute {name!r}"
            )

    def __setattr__(self, name, value):
        if name in _PROXY_OWN:
            # Proxy-internal: store on the proxy object itself.
            object.__setattr__(self, name, value)
        else:
            try:
                legacy = object.__getattribute__(self, "_legacy")
                setattr(legacy, name, value)
            except AttributeError:
                # _legacy not yet initialised (called during __init__).
                object.__setattr__(self, name, value)

    def __dir__(self):
        try:
            legacy = object.__getattribute__(self, "_legacy")
            return sorted(
                set(dir(legacy)) | set(object.__dir__(self))
            )
        except AttributeError:
            return object.__dir__(self)


# Build the proxy and replace this module in sys.modules.
# _legacy_mod is kept alive by the proxy\'s _legacy attribute.
_proxy = _LegacyProxy("app")
object.__setattr__(_proxy, "_legacy", _legacy_mod)
_proxy.__doc__     = __doc__
_proxy.__package__ = _PKG
_proxy.__loader__  = _LOADER
_proxy.__spec__    = _SPEC
_proxy.__path__    = _PATH
_proxy.__file__    = _FILE

sys.modules["app"] = _proxy
'''

# ---------------------------------------------------------------------------
# Check current state — idempotency
# ---------------------------------------------------------------------------
MARKER = "MODULE PROXY"
current = INIT_FILE.read_text() if INIT_FILE.exists() else ""
if MARKER in current:
    print("__init__.py already has the module proxy — nothing to do.")
    sys.exit(0)

# ---------------------------------------------------------------------------
# Write the new __init__.py
# ---------------------------------------------------------------------------
print(f"Writing proxy-based {INIT_FILE.relative_to(ROOT)}...")
INIT_FILE.write_text(INIT_SRC)
print(f"  ✓ {len(INIT_SRC):,} chars written")

# ---------------------------------------------------------------------------
# Byte-compile
# ---------------------------------------------------------------------------
print("\nByte-compiling...")
try:
    py_compile.compile(str(INIT_FILE), doraise=True)
    print(f"  ✓ {INIT_FILE.relative_to(BACKEND)} compiles cleanly")
except py_compile.PyCompileError as exc:
    fail(f"Syntax error in new __init__.py:\n{exc}", code=4)

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
print("""
═══════════════════════════════════════════════════════════════════════
Phase 9 init fix applied.

The __init__.py is now a transparent module proxy: all attribute reads
AND writes are forwarded to legacy.py, so monkeypatch.setattr(A, ...) 
and direct assignment (A.engine = eng, A.AUTH_ENABLED = True, etc.)
reach legacy.py's namespace immediately.

Run the full test suite to confirm all 12 failures are resolved:

  cd backend
  . .venv/bin/activate
  pytest -q

Expected: all tests green (same count as before Phase 9 PR-1).
═══════════════════════════════════════════════════════════════════════
""")
