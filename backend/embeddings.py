"""Pluggable embedding provider for Aptiro (Phase 2).

Same contract as ai_provider.py: the deterministic MOCK is the DEFAULT.
The app and the test suite must run with zero credentials and no
network. A real embedding model is used only when
APTIRO_EMBEDDING_PROVIDER is set to something other than "mock" AND the
matching credentials/SDK are present; otherwise we transparently fall
back to the mock instead of crashing.

The semantic similarity this produces is a *secondary, clearly-labelled*
indicator only. It never feeds the deterministic 0-100 score and never
changes match ranking - that remains the single source of truth. This
module exists so the UI can show "the maths agrees / disagrees" colour
without ever putting an opaque model on the trust path.
"""
import hashlib
import math
import os
import re

_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#.]{1,}")
_DIMS = 256


def _tokens(text):
    return _TOKEN.findall((text or "").lower())


class EmbeddingProvider:
    name = "base"

    def embed(self, text: str) -> list:
        raise NotImplementedError


class MockEmbeddingProvider(EmbeddingProvider):
    """Deterministic, offline, dependency-free.

    A hashed bag-of-tokens projected into a fixed-width unit vector. The
    same text always yields the same vector, so similarity is fully
    reproducible in tests and demos. It is intentionally simple: it is a
    *hint*, not the score.
    """

    name = "mock"

    def embed(self, text):
        vec = [0.0] * _DIMS
        toks = _tokens(text)
        if not toks:
            return vec
        for tok in toks:
            h = hashlib.sha256(tok.encode()).digest()
            idx = int.from_bytes(h[:4], "big") % _DIMS
            sign = 1.0 if h[4] & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """Thin optional wrapper. Only constructed when explicitly selected
    and properly configured; otherwise get_embedding_provider() returns
    the mock. Kept dependency-free unless actually used."""

    name = "openai"

    def __init__(self):
        import openai  # raises if SDK absent -> caller falls back
        self._client = openai.OpenAI(
            api_key=os.environ["OPENAI_API_KEY"])
        self._model = os.getenv("APTIRO_EMBEDDING_MODEL",
                                "text-embedding-3-small")

    def embed(self, text):
        r = self._client.embeddings.create(
            model=self._model, input=text or " ")
        return list(r.data[0].embedding)


_CACHE = {}


def get_embedding_provider() -> EmbeddingProvider:
    """Resolve the active provider. Falls back to mock on any
    misconfiguration so the app never depends on a live key to run."""
    choice = os.getenv("APTIRO_EMBEDDING_PROVIDER", "mock").lower()
    if choice in _CACHE:
        return _CACHE[choice]
    provider: EmbeddingProvider
    if choice == "openai" and os.getenv("OPENAI_API_KEY"):
        try:
            provider = OpenAIEmbeddingProvider()
        except Exception:
            provider = MockEmbeddingProvider()
    else:
        provider = MockEmbeddingProvider()
    _CACHE[choice] = provider
    return provider


def active_embedding_provider_name() -> str:
    return get_embedding_provider().name


def cosine(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    # Clamp to [0, 1]: negative cosines are "no signal", not "anti-fit".
    return round(max(0.0, min(1.0, dot / (na * nb))), 4)
