"""Bounded, cached video downloader for Instagram video DMs via yt-dlp."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from yt_dlp import YoutubeDL

from settings import BASE_DIR, DATA_DIR

LOGGER = logging.getLogger("jinshi_mds.video")

MAX_DURATION_SECONDS = 5 * 60
MAX_FILE_BYTES = 100 * 1024 * 1024  # Instagram rejects videos over ~100 MB
CACHE_MAX_BYTES = 500 * 1024 * 1024
CACHE_MAX_FILES = 20
CACHE_MAX_AGE = 7 * 24 * 60 * 60


@dataclass
class VideoDownload:
    path: Path
    title: str
    work_dir: Path
    cache_hit: bool = False

    def cleanup(self) -> None:
        shutil.rmtree(self.work_dir, ignore_errors=True)


class VideoService:
    def __init__(self) -> None:
        self.cache_dir = DATA_DIR / "video-cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_lock = threading.Lock()
        self.download_locks = [threading.Lock() for _ in range(16)]
        self.archive_lock = threading.Lock()
        self.archive_pending: set[str] = set()
        cookie_file = DATA_DIR / "youtube-cookies.txt"
        if cookie_file.exists():
            cookie_file.chmod(0o600)
        # Clean up stale temp directories from previous restarts
        temp_root = BASE_DIR / "temp"
        if temp_root.exists():
            for old_dir in temp_root.glob("video-*"):
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
        video = self.cache_dir / f"{key}.mp4"
        metadata = self.cache_dir / f"{key}.json"
        with self.cache_lock:
            if not video.exists() or video.stat().st_size <= 0 or time.time() - video.stat().st_mtime > CACHE_MAX_AGE:
                video.unlink(missing_ok=True)
                metadata.unlink(missing_ok=True)
                return None
            try:
                os.link(video, destination)
            except OSError:
                shutil.copy2(video, destination)
            try:
                return str(json.loads(metadata.read_text(encoding="utf-8")).get("title") or query)
            except (OSError, json.JSONDecodeError):
                return query

    def _store_cache(self, query: str, title: str, source: Path) -> None:
        key = self._cache_key(query)
        video = self.cache_dir / f"{key}.mp4"
        metadata = self.cache_dir / f"{key}.json"
        with self.cache_lock:
            temporary = self.cache_dir / f".{key}.tmp"
            temporary.unlink(missing_ok=True)
            try:
                os.link(source, temporary)
            except OSError:
                shutil.copy2(source, temporary)
            temporary.replace(video)
            metadata.write_text(json.dumps({"title": title}), encoding="utf-8")
            self._prune_cache_locked()

    def _prune_cache_locked(self) -> None:
        files = sorted(self.cache_dir.glob("*.mp4"), key=lambda path: path.stat().st_mtime, reverse=True)
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

    @staticmethod
    def _ffmpeg() -> Path:
        executable = shutil.which("ffmpeg")
        if executable:
            return Path(executable)
        bundled = sorted((BASE_DIR / ".browsers").glob("ffmpeg-*/ffmpeg-linux"))
        if bundled:
            return bundled[-1]
        raise RuntimeError("FFmpeg is required for video processing")

    def download(self, query: str) -> VideoDownload:
        query = query.strip()
        if not query:
            raise RuntimeError("A video name or YouTube link is required")
        key = self._cache_key(query)
        lock = self.download_locks[int(key[:2], 16) % len(self.download_locks)]
        with lock:
            return self._download(query)

    def _download_from_youtube(self, target: str, work_dir: Path) -> dict:
        deno_bin = shutil.which("deno") or str(Path.home() / ".deno" / "bin" / "deno")
        js_runtimes = {"deno": {"path": deno_bin}} if deno_bin and Path(deno_bin).exists() else {}
        options = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "socket_timeout": 15,
            "retries": 2,
            "fragment_retries": 2,
            "concurrent_fragment_downloads": 4,
            "ffmpeg_location": str(self._ffmpeg()),
            "format": (
                "18/22/best[ext=mp4][vcodec!=none][acodec!=none][height<=480]/"
                "bestvideo[height<=480]+bestaudio/best[height<=480]/best"
            ),
            "merge_output_format": "mp4",
            "outtmpl": str(work_dir / "source.%(ext)s"),
            "remote_components": {"ejs:github"},
        }
        if js_runtimes:
            options["js_runtimes"] = js_runtimes
        cookie_file = DATA_DIR / "youtube-cookies.txt"
        if cookie_file.exists():
            options["cookiefile"] = str(cookie_file)
        with YoutubeDL(options) as downloader:
            return downloader.extract_info(target, download=True)

    def _download(self, query: str) -> VideoDownload:
        temp_root = BASE_DIR / "temp"
        temp_root.mkdir(parents=True, exist_ok=True)
        work_dir = Path(tempfile.mkdtemp(prefix="video-", dir=temp_root))
        output = work_dir / "video.mp4"

        try:
            cached_title = self._cached(query, output)
            if cached_title:
                self._archive_to_desktop(output, cached_title)
                return VideoDownload(output, cached_title, work_dir, cache_hit=True)

            target = query if query.startswith(("https://", "http://")) else f"ytsearch1:{query}"
            info = self._download_from_youtube(target, work_dir)

            if info and info.get("entries") is not None:
                entries = [entry for entry in info["entries"] if entry]
                info = entries[0] if entries else None
            if not info:
                raise RuntimeError("No matching video was found")

            title = str(info.get("title") or query)
            duration = int(info.get("duration") or 0)
            if duration and duration > MAX_DURATION_SECONDS:
                raise RuntimeError(f"Video is {duration // 60}m{duration % 60}s — exceeds the 5-minute limit")

            downloaded = None
            for candidate in work_dir.glob("source.*"):
                if candidate.stat().st_size > 0:
                    downloaded = candidate
                    break
            if not downloaded:
                raise RuntimeError("yt-dlp did not produce an output file")

            needs_reencode = True
            if downloaded.suffix == ".mp4":
                probe = subprocess.run(
                    [str(self._ffmpeg()), "-nostdin", "-i", str(downloaded)],
                    capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL,
                )
                has_h264 = "h264" in probe.stderr.lower() or "avc1" in probe.stderr.lower()
                has_aac = "aac" in probe.stderr.lower()
                if has_h264 and has_aac:
                    needs_reencode = False
                    downloaded.rename(output)

            if needs_reencode:
                subprocess.run(
                    [
                        str(self._ffmpeg()), "-y", "-nostdin", "-loglevel", "error",
                        "-i", str(downloaded),
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                        "-c:a", "aac", "-b:a", "128k",
                        "-movflags", "+faststart",
                        "-vf", "scale='trunc(min(480,iw)/2)*2':-2",
                        str(output),
                    ],
                    capture_output=True, text=True, timeout=120, check=True, stdin=subprocess.DEVNULL,
                )

            if not output.exists() or output.stat().st_size <= 0:
                raise RuntimeError("Video processing produced an empty file")
            if output.stat().st_size > MAX_FILE_BYTES:
                raise RuntimeError(f"Video is too large ({output.stat().st_size // 1024 // 1024} MB) for Instagram DM delivery")

            self._store_cache(query, title, output)
            video_url = info.get("webpage_url") or info.get("url") or ""
            if video_url and video_url != query:
                self._store_cache(f"youtube:{video_url}", title, output)

            self._archive_to_desktop(output, title)
            return VideoDownload(output, title, work_dir)

        except subprocess.TimeoutExpired as error:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise RuntimeError("Video conversion timed out") from error
        except RuntimeError:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise
        except Exception as error:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise RuntimeError(f"Video download failed: {str(error)[:200]}") from error

    @staticmethod
    def _safe_filename(title: str) -> str:
        safe = "".join(c if c.isalnum() or c in " -_" else "" for c in title).strip()
        return safe[:80] or "video"

    def _archive_to_desktop(self, video_path: Path, title: str) -> None:
        desktop = Path.home() / "Desktop"
        video_dir = desktop / "video"
        audio_dir = desktop / "audio"
        video_dir.mkdir(parents=True, exist_ok=True)
        audio_dir.mkdir(parents=True, exist_ok=True)

        safe_name = self._safe_filename(title)
        video_dest = video_dir / f"{safe_name}.mp4"
        audio_dest = audio_dir / f"{safe_name}.m4a"

        with self.archive_lock:
            if not video_dest.exists():
                try:
                    os.link(video_path, video_dest)
                except OSError:
                    shutil.copy2(video_path, video_dest)
            if audio_dest.exists() or safe_name in self.archive_pending:
                return
            self.archive_pending.add(safe_name)

        threading.Thread(
            target=self._extract_archive_audio,
            args=(safe_name, video_dest, audio_dest),
            name=f"archive-audio-{self._cache_key(safe_name)[:8]}",
            daemon=True,
        ).start()

    def _extract_archive_audio(self, key: str, video_path: Path, audio_path: Path) -> None:
        temp_root = BASE_DIR / "temp"
        temp_root.mkdir(parents=True, exist_ok=True)
        temporary = temp_root / f"part_{key}_{time.time_ns()}.m4a"
        try:
            copy_result = subprocess.run(
                [str(self._ffmpeg()), "-y", "-nostdin", "-loglevel", "error", "-i", str(video_path),
                 "-vn", "-c:a", "copy", str(temporary)],
                capture_output=True, timeout=30, check=False, stdin=subprocess.DEVNULL,
            )
            if copy_result.returncode != 0:
                subprocess.run(
                    [str(self._ffmpeg()), "-y", "-nostdin", "-loglevel", "error", "-i", str(video_path),
                     "-vn", "-c:a", "aac", "-b:a", "128k", str(temporary)],
                    capture_output=True, timeout=45, check=True, stdin=subprocess.DEVNULL,
                )
            if temporary.exists() and temporary.stat().st_size > 0:
                temporary.replace(audio_path)
        except Exception as error:
            temporary.unlink(missing_ok=True)
            LOGGER.warning("Could not archive audio for %s: %s", video_path.name, error)
        finally:
            with self.archive_lock:
                self.archive_pending.discard(key)
