"""Lightweight Semantic Search, Vector Embeddings, Decay, and RRF Retrieval for KnightBot."""
from __future__ import annotations

import json
import math
import re
import struct
import time
from typing import Any, Sequence
from urllib.request import Request, urlopen


class EmbeddingEngine:
    """Zero-heavy-dependency embedding provider with local Ollama support and pure Python fallback."""

    def __init__(self, base_url: str = "http://127.0.0.1:11434", model: str = "nomic-embed-text") -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dimension = 256
        self._has_ollama_embed: bool | None = None

    def embed_text(self, text: str) -> list[float]:
        """Generate embedding vector using Ollama embeddings API, falling back to hashed sparse projection."""
        clean_text = " ".join(text.strip().split())[:1000]
        if not clean_text:
            return [0.0] * self.dimension

        if self._has_ollama_embed is not False:
            vector = self._embed_ollama(clean_text)
            if vector is not None:
                self._has_ollama_embed = True
                return vector
            self._has_ollama_embed = False

        return self._embed_fallback(clean_text)

    def _embed_ollama(self, text: str) -> list[float] | None:
        payload = json.dumps({"model": self.model, "prompt": text}).encode("utf-8")
        req = Request(
            f"{self.base_url}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            embedding = data.get("embedding")
            if isinstance(embedding, list) and embedding and isinstance(embedding[0], (float, int)):
                return [float(x) for x in embedding]
        except Exception:
            return None
        return None

    def _embed_fallback(self, text: str) -> list[float]:
        """Deterministic subword feature hashing vectorizer (Zero-dependency cosine similarity)."""
        dim = self.dimension
        vector = [0.0] * dim
        tokens = re.findall(r"\w+", text.lower())
        if not tokens:
            return vector

        features: list[str] = list(tokens)
        for token in tokens:
            if len(token) >= 3:
                for i in range(len(token) - 2):
                    features.append(token[i : i + 3])

        import zlib
        for feat in features:
            h = zlib.crc32(feat.encode("utf-8"))
            idx = h % dim
            sign = 1.0 if (h % 2 == 0) else -1.0
            vector[idx] += sign

        norm = math.sqrt(sum(v * v for v in vector))
        if norm > 0:
            vector = [v / norm for v in vector]
        return vector

    @staticmethod
    def pack_vector(vector: Sequence[float]) -> bytes:
        """Pack float array into compact binary SQLite BLOB."""
        return struct.pack(f"{len(vector)}f", *vector)

    @staticmethod
    def unpack_vector(blob: bytes) -> list[float]:
        """Unpack binary SQLite BLOB into float list."""
        if not blob:
            return []
        count = len(blob) // 4
        return list(struct.unpack(f"{count}f", blob))

    @staticmethod
    def cosine_similarity(v1: Sequence[float], v2: Sequence[float]) -> float:
        """Compute cosine similarity between two float vectors."""
        if not v1 or not v2 or len(v1) != len(v2):
            return 0.0
        dot = sum(a * b for a, b in zip(v1, v2))
        norm1 = math.sqrt(sum(a * a for a in v1))
        norm2 = math.sqrt(sum(b * b for b in v2))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return max(-1.0, min(1.0, dot / (norm1 * norm2)))


def sqlite_cosine_blob(blob1: bytes, blob2: bytes) -> float:
    """SQLite User-Defined Function (UDF) for cosine similarity on binary packed vectors."""
    if not blob1 or not blob2 or len(blob1) != len(blob2):
        return 0.0
    count = len(blob1) // 4
    v1 = struct.unpack(f"{count}f", blob1)
    v2 = struct.unpack(f"{count}f", blob2)
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 <= 0 or norm2 <= 0:
        return 0.0
    return float(dot / (norm1 * norm2))


class MemoryDecay:
    """Ebbinghaus-inspired exponential forgetting curve with emotional salience & repetition boost."""

    @staticmethod
    def calculate_retention(
        created_at_epoch: float,
        last_recalled_at_epoch: float,
        significance: int = 5,
        recall_count: int = 0,
        decay_constant: float = 0.05,
    ) -> float:
        """Calculate memory retention score R in [0.0, 1.0]."""
        now = time.time()
        reference_time = max(created_at_epoch, last_recalled_at_epoch)
        delta_days = max(0.0, (now - reference_time) / 86400.0)

        s_factor = max(1.0, float(significance)) * (1.0 + math.log1p(max(0, recall_count)))
        exponent = -1.0 * (decay_constant * delta_days) / s_factor
        return math.exp(max(-20.0, exponent))


class HybridRanker:
    """Reciprocal Rank Fusion (RRF) combining sparse BM25 and dense vector search with decay weighting."""

    @staticmethod
    def fuse_results(
        fts_results: list[dict[str, Any]],
        vec_results: list[dict[str, Any]],
        k: int = 60,
        alpha: float = 0.4,
    ) -> list[dict[str, Any]]:
        """Combine FTS rank and Dense rank using weighted RRF."""
        scores: dict[int, float] = {}
        item_map: dict[int, dict[str, Any]] = {}

        for rank, item in enumerate(fts_results):
            m_id = item["id"]
            item_map[m_id] = item
            rrf_score = alpha * (1.0 / (k + rank + 1))
            scores[m_id] = scores.get(m_id, 0.0) + rrf_score

        for rank, item in enumerate(vec_results):
            m_id = item["id"]
            if m_id not in item_map:
                item_map[m_id] = item
            rrf_score = (1.0 - alpha) * (1.0 / (k + rank + 1))
            scores[m_id] = scores.get(m_id, 0.0) + rrf_score

        for m_id, item in item_map.items():
            retention = item.get("retention", 1.0)
            scores[m_id] = scores.get(m_id, 0.0) * (0.3 + 0.7 * retention)

        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
        return [item_map[m_id] for m_id in sorted_ids]
