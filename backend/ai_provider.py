"""Pluggable AI provider for Aptiro.

Decision 2: deterministic mock is the DEFAULT. The app must run and the
test suite must pass with zero credentials and no network. Anthropic is
available only when APTIRO_AI_PROVIDER=anthropic AND ANTHROPIC_API_KEY
is set AND the SDK is installed; otherwise we transparently fall back to
the mock instead of crashing.

This slice does not put AI on the critical path (extraction and export
stay deterministic and grounded). The provider exists so later phases
can call get_provider().complete(...) for optional, clearly-labeled,
non-grounding assistance (e.g. phrasing suggestions) without ever
fabricating resume facts.
"""
import hashlib
import os


class AIProvider:
    name = "base"
    grounded_only = True  # never returns content treated as source truth

    def complete(self, prompt: str, *, system: str = "",
                 max_tokens: int = 512) -> str:
        raise NotImplementedError


class MockProvider(AIProvider):
    """Deterministic, offline, dependency-free. Same input -> same
    output, so tests and demos are reproducible."""

    name = "mock"

    def complete(self, prompt, *, system="", max_tokens=512):
        h = hashlib.sha256((system + "\n" + prompt).encode()).hexdigest()
        return ("[mock-ai] deterministic suggestion (no live model, no "
                "network). This text is advisory only and is never "
                "treated as a grounded resume claim. ref=%s" % h[:12])


class AnthropicProvider(AIProvider):
    """Thin optional wrapper. Only constructed when explicitly selected
    and properly configured; otherwise get_provider() returns the mock.
    """

    name = "anthropic"

    def __init__(self):
        import anthropic  # raises if SDK absent -> caller falls back
        self._client = anthropic.Anthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"])
        self._model = os.getenv("APTIRO_ANTHROPIC_MODEL",
                                "claude-sonnet-4-5")

    def complete(self, prompt, *, system="", max_tokens=512):
        msg = self._client.messages.create(
            model=self._model, max_tokens=max_tokens,
            system=system or "You are a careful resume assistant. Never "
            "invent facts, metrics, employers, titles, or dates. Only "
            "rephrase or organize text the user already provided.",
            messages=[{"role": "user", "content": prompt}])
        return "".join(getattr(b, "text", "") for b in msg.content)


_CACHE = {}


def get_provider() -> AIProvider:
    """Resolve the active provider. Falls back to mock on any
    misconfiguration so the app never depends on a live key to run."""
    choice = os.getenv("APTIRO_AI_PROVIDER", "mock").lower()
    if choice in _CACHE:
        return _CACHE[choice]
    provider: AIProvider
    if choice == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
        try:
            provider = AnthropicProvider()
        except Exception:
            provider = MockProvider()
    else:
        provider = MockProvider()
    _CACHE[choice] = provider
    return provider


def active_provider_name() -> str:
    return get_provider().name
