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
    The proxy's own attrs (__name__, __path__, _legacy, etc.) are stored
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
# _legacy_mod is kept alive by the proxy's _legacy attribute.
_proxy = _LegacyProxy("app")
object.__setattr__(_proxy, "_legacy", _legacy_mod)
_proxy.__doc__     = __doc__
_proxy.__package__ = _PKG
_proxy.__loader__  = _LOADER
_proxy.__spec__    = _SPEC
_proxy.__path__    = _PATH
_proxy.__file__    = _FILE

sys.modules["app"] = _proxy
