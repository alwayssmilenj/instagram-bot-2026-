"""Bounded, cached song downloader for Instagram voice messages."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from yt_dlp import YoutubeDL

from settings import BASE_DIR, DATA_DIR

MAX_DURATION_SECONDS = 10 * 60
MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_FILE_BYTES = 15 * 1024 * 1024
CACHE_MAX_BYTES = 300 * 1024 * 1024
CACHE_MAX_FILES = 30
CACHE_MAX_AGE = 7 * 24 * 60 * 60


@dataclass
class SongDownload:
    path: Path
    title: str
    work_dir: Path
    cache_hit: bool = False

    def cleanup(self) -> None:
        shutil.rmtree(self.work_dir, ignore_errors=True)


class SongService:
    def __init__(self) -> None:
        self.cache_dir = DATA_DIR / "song-cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_lock = threading.Lock()
        self.download_locks = [threading.Lock() for _ in range(16)]
        temp_root = BASE_DIR / "temp"
        if temp_root.exists():
            for old_dir in temp_root.glob("song-*"):
                try:
                    shutil.rmtree(old_dir, ignore_errors=True)
                except Exception:
                    pass

    @staticmethod
    def _cache_key(query: str) -> str:
        normalized = " ".join(query.lower().split())
        return hashlib.sha256(normalized.encode()).hexdigest()

    def _cached(self, query: str, destination: Path) -> str | None:
        key = self._cache_key(query)
        audio = self.cache_dir / f"{key}.m4a"
        metadata = self.cache_dir / f"{key}.json"
        with self.cache_lock:
            if not audio.exists() or audio.stat().st_size <= 0 or time.time() - audio.stat().st_mtime > CACHE_MAX_AGE:
                audio.unlink(missing_ok=True)
                metadata.unlink(missing_ok=True)
                return None
            try:
                os.link(audio, destination)
            except OSError:
                shutil.copy2(audio, destination)
            try:
                return str(json.loads(metadata.read_text(encoding="utf-8")).get("title") or query)
            except (OSError, json.JSONDecodeError):
                return query

    def _store_cache(self, query: str, title: str, source: Path) -> None:
        key = self._cache_key(query)
        audio = self.cache_dir / f"{key}.m4a"
        metadata = self.cache_dir / f"{key}.json"
        with self.cache_lock:
            temporary = self.cache_dir / f".{key}.tmp"
            temporary.unlink(missing_ok=True)
            try:
                os.link(source, temporary)
            except OSError:
                shutil.copy2(source, temporary)
            temporary.replace(audio)
            metadata.write_text(json.dumps({"title": title}), encoding="utf-8")
            self._prune_cache_locked()

    def _prune_cache_locked(self) -> None:
        files = sorted(self.cache_dir.glob("*.m4a"), key=lambda path: path.stat().st_mtime, reverse=True)
        total = 0
        for index, path in enumerate(files):
            try:
                size = path.stat().st_size
                total += size
                if index >= CACHE_MAX_FILES or total > CACHE_MAX_BYTES or time.time() - path.stat().st_mtime > CACHE_MAX_AGE:
                    path.unlink(missing_ok=True)
                    path.with_suffix(".json").unlink(missing_ok=True)
            except OSError:
                pass

    def _resolve_youtube(self, query: str) -> tuple[str, str, int]:
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "extract_flat": "in_playlist",
            "socket_timeout": 10,
        }
        cookie_file = DATA_DIR / "youtube-cookies.txt"
        if cookie_file.exists():
            options["cookiefile"] = str(cookie_file)
        target = query if query.startswith(("https://", "http://")) else f"ytsearch1:{query}"
        with YoutubeDL(options) as downloader:
            info = downloader.extract_info(target, download=False)
        if info and info.get("entries") is not None:
            entries = [entry for entry in info["entries"] if entry]
            info = entries[0] if entries else None
        if not info:
            raise RuntimeError("No matching song was found")
        video_id = str(info.get("id") or "")
        url = str(info.get("webpage_url") or "")
        if not url.startswith("http") and video_id:
            url = f"https://www.youtube.com/watch?v={video_id}"
        duration = int(info.get("duration") or 0)
        if not url:
            raise RuntimeError("Song search returned no YouTube URL")
        if duration and duration > MAX_DURATION_SECONDS:
            raise RuntimeError("Song is longer than the 10-minute limit")
        return url, str(info.get("title") or query), duration

    @staticmethod
    def _provider_url(youtube_url: str) -> str:
        response = requests.get(
            "https://eliteprotech-apis.zone.id/ytdown",
            params={"url": youtube_url, "format": "mp3", "_": str(time.time_ns())},
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        download_url = data.get("downloadURL")
        if not data.get("success") or not download_url:
            raise RuntimeError("Audio provider returned no download")
        return str(download_url)

    @staticmethod
    def _download_source(url: str, destination: Path) -> None:
        total = 0
        with requests.get(url, stream=True, timeout=(10, 30), headers={"User-Agent": "Mozilla/5.0"}) as response:
            response.raise_for_status()
            if int(response.headers.get("content-length") or 0) > MAX_SOURCE_BYTES:
                raise RuntimeError("Song source exceeds 20 MB")
            with destination.open("wb") as output:
                for chunk in response.iter_content(128 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_SOURCE_BYTES:
                        raise RuntimeError("Song source exceeds 20 MB")
                    output.write(chunk)
        if total == 0:
            raise RuntimeError("Audio provider returned an empty file")

    def _download_via_ytdlp(self, youtube_url: str, work_dir: Path, output: Path) -> None:
        options = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "socket_timeout": 15,
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": str(work_dir / "ytdlp_audio.%(ext)s"),
            "ffmpeg_location": str(self._ffmpeg()),
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
                "preferredquality": "128",
            }],
        }
        cookie_file = DATA_DIR / "youtube-cookies.txt"
        if cookie_file.exists():
            options["cookiefile"] = str(cookie_file)
        with YoutubeDL(options) as downloader:
            downloader.extract_info(youtube_url, download=True)
        extracted = work_dir / "ytdlp_audio.m4a"
        if extracted.exists() and extracted.stat().st_size > 0:
            extracted.rename(output)
        else:
            candidates = list(work_dir.glob("ytdlp_audio.*"))
            if candidates:
                subprocess.run(
                    [
                        str(self._ffmpeg()), "-y", "-nostdin", "-loglevel", "error",
                        "-i", str(candidates[0]), "-vn", "-c:a", "aac", "-b:a", "128k",
                        "-movflags", "+faststart", str(output),
                    ],
                    capture_output=True, text=True, timeout=45, check=True, stdin=subprocess.DEVNULL,
                )

    @staticmethod
    def _ffmpeg() -> Path:
        executable = shutil.which("ffmpeg")
        if executable:
            return Path(executable)
        bundled = sorted((BASE_DIR / ".browsers").glob("ffmpeg-*/ffmpeg-linux"))
        if bundled:
            return bundled[-1]
        raise RuntimeError("Full FFmpeg is required to convert this audio format")

    def download(self, query: str) -> SongDownload:
        query = query.strip()
        if not query:
            raise RuntimeError("A song name or YouTube link is required")
        key = self._cache_key(query)
        lock = self.download_locks[int(key[:2], 16) % len(self.download_locks)]
        with lock:
            return self._download(query)

    def _download(self, query: str) -> SongDownload:
        temp_root = BASE_DIR / "temp"
        temp_root.mkdir(parents=True, exist_ok=True)
        work_dir = Path(tempfile.mkdtemp(prefix="song-", dir=temp_root))
        source, output = work_dir / "source.audio", work_dir / "voice.m4a"
        try:
            cached_title = self._cached(query, output)
            if cached_title:
                self._archive_to_desktop(output, cached_title)
                return SongDownload(output, cached_title, work_dir, cache_hit=True)

            youtube_url, title, _ = self._resolve_youtube(query)
            video_cache_key = f"youtube:{youtube_url}"
            cached_title = self._cached(video_cache_key, output)
            if cached_title:
                self._store_cache(query, cached_title, output)
                self._archive_to_desktop(output, cached_title)
                return SongDownload(output, cached_title, work_dir, cache_hit=True)

            download_succeeded = False
            for attempt in range(3):
                try:
                    self._download_source(self._provider_url(youtube_url), source)
                    download_succeeded = True
                    break
                except (requests.RequestException, RuntimeError, json.JSONDecodeError):
                    source.unlink(missing_ok=True)
                    if attempt < 2:
                        time.sleep(0.5 * (attempt + 1))

            if not download_succeeded:
                # Direct yt-dlp audio fallback
                try:
                    self._download_via_ytdlp(youtube_url, work_dir, output)
                except Exception as ytdlp_err:
                    raise RuntimeError(f"Audio download failed: {ytdlp_err}") from ytdlp_err
            else:
                conversion = subprocess.run(
                    [
                        str(self._ffmpeg()), "-y", "-nostdin", "-loglevel", "error",
                        "-i", str(source), "-vn", "-c:a", "aac", "-b:a", "128k",
                        "-movflags", "+faststart", str(output),
                    ],
                    capture_output=True, text=True, timeout=45, check=False, stdin=subprocess.DEVNULL,
                )
                if conversion.returncode != 0:
                    raise RuntimeError((conversion.stderr or "FFmpeg conversion failed")[-300:])

            if not output.exists() or not 0 < output.stat().st_size <= MAX_FILE_BYTES:
                raise RuntimeError("Converted song is empty or over 15 MB")

            self._store_cache(query, title, output)
            self._store_cache(video_cache_key, title, output)
            self._archive_to_desktop(output, title)
            return SongDownload(output, title, work_dir)

        except subprocess.TimeoutExpired as error:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise RuntimeError("Audio conversion timed out") from error
        except Exception:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise

    @staticmethod
    def _safe_filename(title: str) -> str:
        safe = "".join(character if character.isalnum() or character in " -_" else "" for character in title).strip()
        return safe[:80] or "audio"

    def _archive_to_desktop(self, source: Path, title: str) -> None:
        destination_dir = Path.home() / "Desktop" / "audio"
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"{self._safe_filename(title)}.m4a"
        if destination.exists():
            return
        try:
            os.link(source, destination)
        except OSError:
            shutil.copy2(source, destination)
