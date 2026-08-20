"""Fast, bounded lyrics lookup with a persistent local cache."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from commands.core import clean_media_query
from settings import DATA_DIR

CACHE_MAX_AGE = 7 * 24 * 60 * 60
CACHE_MAX_FILES = 100
MAX_LYRICS_CHARS = 18000


@dataclass(frozen=True)
class LyricsResult:
    title: str
    artist: str
    lyrics: str
    cache_hit: bool = False

    def chunks(self, maximum: int = 1550, limit: int = 12) -> list[str]:
        lines = self.lyrics.splitlines()
        chunks: list[str] = []
        current = ""
        for line in lines:
            pieces = [line[index:index + maximum] for index in range(0, len(line), maximum)] or [""]
            for piece in pieces:
                candidate = f"{current}\n{piece}".strip() if current else piece
                if current and len(candidate) > maximum:
                    chunks.append(current)
                    current = piece
                else:
                    current = candidate
                if len(chunks) >= limit:
                    return chunks
        if current and len(chunks) < limit:
            chunks.append(current)
        return chunks


class LyricsService:
    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or DATA_DIR / "lyrics-cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.locks = [threading.Lock() for _ in range(16)]

    @staticmethod
    def _key(query: str) -> str:
        normalized = " ".join(query.lower().split())
        return hashlib.sha256(normalized.encode()).hexdigest()

    def _cache_path(self, query: str) -> Path:
        return self.cache_dir / f"{self._key(query)}.json"

    def _read_cache(self, query: str) -> LyricsResult | None:
        path = self._cache_path(query)
        try:
            if time.time() - path.stat().st_mtime > CACHE_MAX_AGE:
                path.unlink(missing_ok=True)
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            return LyricsResult(
                title=str(data["title"]), artist=str(data["artist"]),
                lyrics=str(data["lyrics"]), cache_hit=True,
            )
        except (FileNotFoundError, KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            return None

    def _write_cache(self, query: str, result: LyricsResult) -> None:
        path = self._cache_path(query)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "title": result.title,
            "artist": result.artist,
            "lyrics": result.lyrics,
        }, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
        files = sorted(self.cache_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        for stale in files[CACHE_MAX_FILES:]:
            stale.unlink(missing_ok=True)

    def fetch(self, query: str) -> LyricsResult:
        query = clean_media_query(query)[:200]
        if not query:
            raise ValueError("A song title is required")
        key = self._key(query)
        lock = self.locks[int(key[:2], 16) % len(self.locks)]
        with lock:
            cached = self._read_cache(query)
            if cached is not None:
                return cached
            response = requests.get(
                "https://lrclib.net/api/search",
                params={"q": query},
                headers={"User-Agent": "ineffa-instagram-bot/1.0"},
                timeout=(2, 6),
            )
            response.raise_for_status()
            matches = response.json()
            if not isinstance(matches, list):
                raise RuntimeError("Lyrics provider returned an invalid response")
            match = next((item for item in matches if isinstance(item, dict) and item.get("plainLyrics")), None)
            if match is None:
                raise LookupError("No lyrics found for that song")
            lyrics = str(match["plainLyrics"]).replace("\r\n", "\n").replace("\r", "\n").strip()
            if not lyrics:
                raise LookupError("No lyrics found for that song")
            result = LyricsResult(
                title=str(match.get("trackName") or query)[:150],
                artist=str(match.get("artistName") or "Unknown artist")[:100],
                lyrics=lyrics[:MAX_LYRICS_CHARS],
            )
            self._write_cache(query, result)
            return result
