"""Provider-agnostic embedding interfaces with local sentence-transformers and TF-IDF fallbacks."""

from __future__ import annotations

import collections
import hashlib
import logging
import math
import re
from abc import ABC, abstractmethod
from typing import List, Optional
import httpx

from job_radar.config import settings

logger = logging.getLogger(__name__)


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """Compute cosine similarity between two float vectors."""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    norm_a = math.sqrt(sum(a * a for a in v1))
    norm_b = math.sqrt(sum(b * b for b in v2))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


class BaseEmbeddingClient(ABC):
    """Abstract embedding client."""

    @abstractmethod
    async def embed_text(self, text: str) -> List[float]:
        pass

    @abstractmethod
    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        pass


class SimpleHashingVectorizer:
    """
    Lightweight, fast local bag-of-words / character-trigram hashing vectorizer.
    Produces 128-dimensional L2-normalized float vectors on CPU without PyTorch or external downloads.
    """

    def __init__(self, dim: int = 128):
        self.dim = dim

    def transform(self, text: str) -> List[float]:
        tokens = re.findall(r"\b[a-zA-Z0-9_+#.-]{2,}\b", text.lower())
        if not tokens:
            return [0.0] * self.dim

        vec = [0.0] * self.dim
        for token in tokens:
            # Word token
            h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % self.dim
            vec[h] += 1.0
            # Character trigrams for morphological and misspelling robustness
            for i in range(len(token) - 2):
                tri = token[i : i + 3]
                h_tri = int(hashlib.md5(tri.encode("utf-8")).hexdigest(), 16) % self.dim
                vec[h_tri] += 0.3

        # L2 normalize
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec


class LocalEmbeddingClient(BaseEmbeddingClient):
    """
    Local embedding using sentence-transformers if installed,
    otherwise automatically falling back to SimpleHashingVectorizer.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._st_model = None
        self._hashing_fallback = SimpleHashingVectorizer(dim=128)
        self._init_st()

    def _init_st(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
            self._st_model = SentenceTransformer(self.model_name)
            logger.info(f"Loaded sentence-transformers model '{self.model_name}' successfully.")
        except Exception as e:
            logger.info(f"sentence-transformers not available ({e}). Using fast built-in hashing vectorizer.")
            self._st_model = None

    async def embed_text(self, text: str) -> List[float]:
        if self._st_model is not None:
            try:
                emb = self._st_model.encode(text, convert_to_numpy=True)
                return [float(x) for x in emb.tolist()]
            except Exception as e:
                logger.warning(f"sentence-transformers inference failed: {e}. Using fallback.")
        return self._hashing_fallback.transform(text)

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        if self._st_model is not None:
            try:
                embs = self._st_model.encode(texts, convert_to_numpy=True)
                return [[float(x) for x in row.tolist()] for row in embs]
            except Exception as e:
                logger.warning(f"sentence-transformers batch failed: {e}. Using fallback.")
        return [self._hashing_fallback.transform(t) for t in texts]


class GeminiEmbeddingClient(BaseEmbeddingClient):
    """Google Gemini text-embedding-004 API."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.gemini_api_key
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent"

    async def embed_text(self, text: str) -> List[float]:
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY required for Gemini embeddings.")
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{self.base_url}?key={self.api_key}",
                json={"content": {"parts": [{"text": text[:2048]}]}},
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()["embedding"]["values"]

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        results = []
        for text in texts:
            emb = await self.embed_text(text)
            results.append(emb)
        return results


class OpenAIEmbeddingClient(BaseEmbeddingClient):
    """OpenAI text-embedding-3-small API."""

    def __init__(self, api_key: Optional[str] = None, model: str = "text-embedding-3-small"):
        self.api_key = api_key or settings.openai_api_key
        self.model = model
        self.base_url = "https://api.openai.com/v1/embeddings"

    async def embed_text(self, text: str) -> List[float]:
        res = await self.embed_batch([text])
        return res[0]

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY required for OpenAI embeddings.")
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(
                self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": [t[:4000] for t in texts]},
            )
            resp.raise_for_status()
            data = resp.json()
            return [item["embedding"] for item in data["data"]]


class MockEmbeddingClient(BaseEmbeddingClient):
    """Deterministic mock embedding client for testing."""

    def __init__(self, dim: int = 64):
        self.dim = dim
        self._vectorizer = SimpleHashingVectorizer(dim=dim)

    async def embed_text(self, text: str) -> List[float]:
        return self._vectorizer.transform(text)

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self._vectorizer.transform(t) for t in texts]


def get_embedding_client() -> BaseEmbeddingClient:
    """Factory returning the configured embedding client."""
    provider = settings.embedding_provider.lower().strip()
    if provider == "local":
        return LocalEmbeddingClient()
    elif provider == "gemini":
        if settings.gemini_api_key:
            return GeminiEmbeddingClient()
        logger.warning("GEMINI_API_KEY not set; falling back to LocalEmbeddingClient.")
        return LocalEmbeddingClient()
    elif provider == "openai":
        if settings.openai_api_key:
            return OpenAIEmbeddingClient()
        logger.warning("OPENAI_API_KEY not set; falling back to LocalEmbeddingClient.")
        return LocalEmbeddingClient()
    elif provider == "mock":
        return MockEmbeddingClient()

    return LocalEmbeddingClient()
