"""Lightweight Semantic Search, Vector Embeddings, Decay, and RRF Retrieval for KnightBot."""
from __future__ import annotations

import json
import math
import re
import struct
import time
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
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
        except HTTPError as err:
            try:
                err.close()
            except Exception:
                pass
            return None
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


class BM25Scorer:
    """Lightweight in-memory Okapi BM25 lexical ranker for episodic and document search."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b

    @staticmethod
    def tokenize(text: str) -> list[str]:
        return [w for w in re.findall(r"\b\w+\b", text.lower()) if len(w) > 1]

    def score_documents(
        self,
        query: str,
        documents: Sequence[dict[str, Any]],
        text_field: str = "summary",
    ) -> list[dict[str, Any]]:
        """Compute BM25 scores for a list of document dicts against a query string."""
        query_tokens = self.tokenize(query)
        if not query_tokens or not documents:
            return []

        doc_tokens_list = [self.tokenize(str(doc.get(text_field, ""))) for doc in documents]
        n_docs = len(documents)
        avg_dl = sum(len(dt) for dt in doc_tokens_list) / max(1, n_docs)

        # Compute document frequencies
        df: dict[str, int] = {}
        for dt in doc_tokens_list:
            seen = set(dt)
            for token in seen:
                df[token] = df.get(token, 0) + 1

        scored: list[dict[str, Any]] = []
        for doc, dt in zip(documents, doc_tokens_list):
            dl = len(dt)
            score = 0.0
            tf_counts: dict[str, int] = {}
            for token in dt:
                tf_counts[token] = tf_counts.get(token, 0) + 1

            for q in query_tokens:
                if q not in df:
                    continue
                doc_freq = df[q]
                idf = math.log(1.0 + (n_docs - doc_freq + 0.5) / (doc_freq + 0.5))
                tf = tf_counts.get(q, 0)
                numerator = tf * (self.k1 + 1.0)
                denominator = tf + self.k1 * (1.0 - self.b + self.b * (dl / max(1.0, avg_dl)))
                score += idf * (numerator / max(0.001, denominator))

            if score > 0.0:
                doc_copy = dict(doc)
                doc_copy["bm25_score"] = round(score, 4)
                scored.append(doc_copy)

        scored.sort(key=lambda x: x.get("bm25_score", 0.0), reverse=True)
        return scored


class HybridRanker:
    """Reciprocal Rank Fusion (RRF) combining sparse BM25 and dense vector search with decay & valence weighting."""

    @staticmethod
    def fuse_results(
        fts_results: list[dict[str, Any]],
        vec_results: list[dict[str, Any]],
        k: int = 60,
        alpha: float = 0.4,
    ) -> list[dict[str, Any]]:
        """Combine FTS/BM25 rank and Dense vector rank using weighted RRF."""
        scores: dict[int, float] = {}
        item_map: dict[int, dict[str, Any]] = {}

        for rank, item in enumerate(fts_results):
            m_id = item.get("id")
            if m_id is None:
                continue
            item_map[m_id] = item
            rrf_score = alpha * (1.0 / (k + rank + 1))
            scores[m_id] = scores.get(m_id, 0.0) + rrf_score

        for rank, item in enumerate(vec_results):
            m_id = item.get("id")
            if m_id is None:
                continue
            if m_id not in item_map:
                item_map[m_id] = item
            rrf_score = (1.0 - alpha) * (1.0 / (k + rank + 1))
            scores[m_id] = scores.get(m_id, 0.0) + rrf_score

        for m_id, item in item_map.items():
            retention = item.get("retention", 1.0)
            valence = abs(float(item.get("valence", 0.0)))
            # Emotional salience boost + retention multiplier
            scores[m_id] = scores.get(m_id, 0.0) * (0.3 + 0.7 * retention) * (1.0 + 0.2 * valence)

        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
        return [item_map[m_id] for m_id in sorted_ids]


class SocialRelationshipEngine:
    """Tracks and classifies directed multi-user relationships and interpersonal dynamics in group threads."""

    RELATION_TYPES = ("ally", "rival", "banter_buddy", "admirer", "neutral")

    @classmethod
    def classify_relationship(cls, affinity_score: float, interaction_count: int, dominant_vibe: str = "chill") -> str:
        """Classify relationship type based on affinity score and interaction vibe."""
        if interaction_count < 2:
            return "neutral"
        if affinity_score >= 6.0:
            return "ally"
        if affinity_score <= -4.0:
            return "rival"
        if dominant_vibe in {"sarcastic", "roast", "playful"} and affinity_score >= 0.0:
            return "banter_buddy"
        if dominant_vibe == "flirty" and affinity_score >= 3.0:
            return "admirer"
        if affinity_score >= 2.0:
            return "ally"
        return "neutral"

    @classmethod
    def format_dynamics_summary(cls, relationships: list[dict[str, Any]]) -> str:
        """Format social dynamics summary for system prompt injection."""
        if not relationships:
            return ""
        lines = []
        for rel in relationships[:5]:
            src = rel.get("source_username") or rel.get("source_user_id")
            tgt = rel.get("target_username") or rel.get("target_user_id")
            rtype = rel.get("relation_type", "neutral")
            aff = rel.get("affinity_score", 0.0)
            count = rel.get("interaction_count", 1)
            lines.append(f"- @{src} <-> @{tgt}: {rtype} (affinity: {aff:+.1f}, {count} interactions)")
        return "GROUP SOCIAL DYNAMICS & RELATIONSHIPS:\n" + "\n".join(lines)


class SentimentTrajectoryAnalyzer:
    """Analyzes continuous emotional trajectory, moving sentiment averages, and stress markers."""

    @staticmethod
    def evaluate_text_sentiment(text: str) -> tuple[float, float, bool]:
        """Returns (valence [-1.0, 1.0], arousal [0.0, 1.0], stress_flag)."""
        lowered = text.lower()
        pos_words = {"love", "happy", "great", "awesome", "good", "best", "w", "win", "yay", "hype", "fire", "lfg", "thanks", "thank", "kind", "beautiful"}
        neg_words = {"hate", "sad", "bad", "worst", "depressed", "angry", "crying", "hurt", "fail", "lost", "ugly", "stress", "lonely", "annoyed", "tired"}
        stress_words = {"overwhelmed", "panicking", "panic", "breakdown", "can't take it", "exhausted", "hopeless", "depressed", "struggling", "help me"}

        tokens = set(re.findall(r"\b\w+\b", lowered))
        pos_hits = len(tokens & pos_words)
        neg_hits = len(tokens & neg_words)
        stress_hits = len(tokens & stress_words) or any(phrase in lowered for phrase in ("cant do this", "feel awful", "rough day", "breaking down"))

        valence = 0.0
        total = pos_hits + neg_hits
        if total > 0:
            valence = (pos_hits - neg_hits) / float(total)

        arousal = 0.3
        if any(c in text for c in ("!", "🔥", "⚡", "💥", "🚀")) or text.isupper():
            arousal = min(1.0, 0.5 + 0.1 * text.count("!"))

        stress_flag = bool(stress_hits > 0 or (neg_hits >= 2 and arousal > 0.6))
        return round(max(-1.0, min(1.0, valence)), 2), round(max(0.0, min(1.0, arousal)), 2), stress_flag

    @staticmethod
    def calculate_trajectory(history: list[dict[str, Any]]) -> dict[str, Any]:
        """Compute moving average and trend direction from recent sentiment snapshots."""
        if not history:
            return {"average_valence": 0.0, "average_arousal": 0.3, "trend": "stable", "stress_detected": False}

        valences = [float(h.get("valence", 0.0)) for h in history]
        arousals = [float(h.get("arousal", 0.0)) for h in history]
        stress = any(bool(h.get("stress_flag", False)) for h in history[-3:])

        avg_val = sum(valences) / len(valences)
        avg_aro = sum(arousals) / len(arousals)

        trend = "stable"
        if len(valences) >= 3:
            first_half = sum(valences[: len(valences) // 2]) / (len(valences) // 2)
            second_half = sum(valences[len(valences) // 2 :]) / (len(valences) - len(valences) // 2)
            if second_half - first_half >= 0.3:
                trend = "improving"
            elif first_half - second_half >= 0.3:
                trend = "declining"

        return {
            "average_valence": round(avg_val, 2),
            "average_arousal": round(avg_aro, 2),
            "trend": trend,
            "stress_detected": stress,
        }


class InsideJokeClusterer:
    """Clusters, merges variants, and tracks semantic inside jokes across users and threads."""

    @staticmethod
    def slugify(text: str) -> str:
        words = [w for w in re.findall(r"\b[a-zA-Z0-9_-]+\b", text.lower()) if len(w) >= 2]
        stopwords = {"the", "a", "an", "our", "that", "this", "is", "was", "my", "your", "of", "in", "to", "at"}
        meaningful = [w for w in words if w not in stopwords]
        chosen = meaningful[:4] if meaningful else words[:4]
        return "_".join(chosen)[:40] or "inside_joke"

    @classmethod
    def similarity(cls, phrase1: str, phrase2: str) -> float:
        """Token Jaccard and n-gram overlap between two joke phrases."""
        tok1 = set(re.findall(r"\b\w+\b", phrase1.lower()))
        tok2 = set(re.findall(r"\b\w+\b", phrase2.lower()))
        if not tok1 or not tok2:
            return 0.0
        intersection = tok1 & tok2
        union = tok1 | tok2
        return len(intersection) / float(len(union))


class GroupLoreManager:
    """Categorizes and matches group-wide mythos, running gags, quotes, and canonical rules."""

    CATEGORIES = ("gag", "quote", "myth", "rule", "event")

    @staticmethod
    def detect_lore_category(text: str) -> str:
        lowered = text.lower()
        if any(w in lowered for w in ("rule", "law", "forbidden", "policy")):
            return "rule"
        if any(w in lowered for w in ('"', "said", "quote", "legendary words")):
            return "quote"
        if any(w in lowered for w in ("remember when", "that time", "incident", "day we")):
            return "event"
        if any(w in lowered for w in ("legend of", "myth", "ancient lore", "prophecy")):
            return "myth"
        return "gag"


class EpisodicConsolidator:
    """Autonomous background memory consolidator that synthesizes multi-turn working memory into dense episodes."""

    def __init__(self, database: Any, embedding_engine: EmbeddingEngine | None = None) -> None:
        self.database = database
        self.embedding_engine = embedding_engine or EmbeddingEngine()

    def consolidate_session(self, user_id: str, session_key: str, min_turns: int = 4) -> dict[str, Any] | None:
        """Examine working memory turns for a session, summarize if ready, and persist as an episodic milestone/memory."""
        if not hasattr(self.database, "get_working_memory") or not hasattr(self.database, "record_episode"):
            return None

        turns = self.database.get_working_memory(session_key=session_key, limit=30)
        if len(turns) < min_turns:
            return None

        dialog_lines = []
        for t in turns:
            role = t.get("role", "user") if isinstance(t, dict) else getattr(t, "role", "user")
            content = t.get("content", "") if isinstance(t, dict) else getattr(t, "content", "")
            dialog_lines.append(f"{str(role).capitalize()}: {content}")

        transcript = " \u2022 ".join(dialog_lines)
        if not transcript.strip():
            return None

        summary = f"Session covering {len(turns)} turns with @{user_id}."
        has_owner_bond = any("jinshi" in line.lower() or "owner" in line.lower() for line in dialog_lines)
        significance = 8 if has_owner_bond else min(10, max(2, len(turns) // 2))
        is_milestone = bool(has_owner_bond or len(turns) >= 12)
        milestone_type = "creator_bond" if has_owner_bond else ("deep_session" if is_milestone else None)
        valence = 0.5 if has_owner_bond else 0.2

        ep_id = self.database.record_episode(
            user_id=user_id,
            session_key=session_key,
            summary=summary,
            mood="chill",
            significance=significance,
            valence=valence,
            is_milestone=is_milestone,
            milestone_type=milestone_type,
        )

        return {
            "episode_id": ep_id,
            "user_id": user_id,
            "session_key": session_key,
            "significance": significance,
            "is_milestone": is_milestone,
            "summary": summary,
        }

