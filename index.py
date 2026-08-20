#!/usr/bin/env python3
"""jinshi_mds: Chromium-authenticated, realtime Instagram DM/group bot."""
from __future__ import annotations

import argparse
import hashlib
import logging
import json
import os
import queue
import re
import shutil
import signal
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import config
import settings
from commands.core import AIRequest, CanvasRequest, GitHubRequest, LyricsRequest, PiesRequest, SearchRequest, SongRequest, StickerRequest, TeachRequest, TTSRequest, VideoRequest, WikiRequest, clean_media_query
from lib.ai_service import AIService
from lib.canvas_service import CanvasService
from lib.chrome_group_remover import ChromeGroupRemover
from lib.database import Database
from lib.gc_monitor import GCMonitor
from lib.github_service import GitHubService
from lib.home_alert import HomeAlertService
from lib.job_queue import PriorityWorkQueue, rss_bytes
from lib.lyrics_service import LyricsService
from lib.message_handler import MessageHandler
from lib.moderation import GroupModerator
from lib.owner_commands import OwnerCommands
from lib.pies_service import PiesService
from lib.search_service import SearchService
from lib.song_service import SongService
from lib.sticker_service import StickerService
from lib.tts_service import TTSService
from lib.video_service import VideoService

LOGGER = logging.getLogger("jinshi_mds")

AUTH_BLOCK_MARKERS = (
    "challenge_required", "challengerequired", "checkpoint", "manual verification",
    "login_required", "loginrequired", "session expired",
)


def requires_manual_verification(error: object) -> bool:
    message = str(error).lower().replace(" ", "_")
    return any(marker.replace(" ", "_") in message for marker in AUTH_BLOCK_MARKERS)


def mark_manual_verification(error: object) -> None:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings.INSTAGRAM_CHALLENGE_FILE.write_text(
        f"{int(time.time())}\n{type(error).__name__}\n",
        encoding="utf-8",
    )
    settings.INSTAGRAM_CHALLENGE_FILE.chmod(0o600)


class JinshiMds:
    def __init__(self) -> None:
        from instagrapi import Client

        self.client = Client()
        self._prune_temp()
        # Keep REST jitter bounded; realtime handles the latency-sensitive path.
        self.client.delay_range = [0.75, 1.75]
        if config.PROXY_URL:
            self.client.set_proxy(config.PROXY_URL)
        self.database = Database()
        self.handler = MessageHandler()
        self.ai_service = AIService(self.database)
        self.group_remover = ChromeGroupRemover()
        self.moderator = GroupModerator(self.client, self.database, self.group_remover)
        self.owner_commands = OwnerCommands(self.database)
        self.home_alert = HomeAlertService(volume_percent=config.HOME_ALERT_VOLUME_PERCENT)
        self.song_service = SongService()
        self.video_service = VideoService()
        self.lyrics_service = LyricsService()
        self.pies_service = PiesService()
        self.sticker_service = StickerService()
        self.search_service = SearchService()
        self.tts_service = TTSService()
        self.canvas_service = CanvasService()
        self.github_service = GitHubService()
        self.gc_monitor = GCMonitor()
        self.running = False
        self.poll_number = 0
        self.wakeup = threading.Event()
        self.realtime_thread_ids: queue.SimpleQueue[tuple[str, object]] = queue.SimpleQueue()
        self.realtime_thread: threading.Thread | None = None
        self.thread_cache: dict[str, tuple[float, object]] = {}
        self.thread_cache_ttl = 120.0
        self.api_lock = threading.RLock()
        self.send_lock = threading.Lock()
        self.send_client = None
        self.media_send_lock = threading.Lock()
        self.media_client = None
        self.realtime_send_lock = threading.Lock()
        self.cache_lock = threading.Lock()
        self.username_cache: dict[str, str] = {}
        self.owner_gate_cooldown: dict[str, float] = {}  # thread_id -> last warning timestamp
        self.banned_user_cooldown: dict[str, float] = {}  # (thread_id, user) -> last warning timestamp
        self.in_flight_media: dict[tuple[int, str, str], float] = {}  # (thread_id, media_type, norm_query) -> start_time
        self.recent_media: dict[tuple[int, str, str], float] = {}  # (thread_id, media_type, norm_query) -> finish_time
        self.media_request_lock = threading.Lock()
        self.jobs = PriorityWorkQueue(
            workers=config.COMMAND_WORKERS,
            maximum=config.COMMAND_QUEUE_MAX,
            max_rss_bytes=config.MAX_RSS_MB * 1024 * 1024,
            emergency_exit=lambda: os._exit(76),
        )

    @staticmethod
    def _prune_temp(max_age_seconds: int = 3600) -> None:
        """Remove abandoned media work directories left by interrupted runs."""
        temp_dir = settings.BASE_DIR / "temp"
        if not temp_dir.exists():
            return
        cutoff = time.time() - max_age_seconds
        for child in temp_dir.iterdir():
            try:
                if child.stat().st_mtime >= cutoff:
                    continue
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
            except OSError:
                LOGGER.debug("Could not prune stale temporary item %s", child)

    @staticmethod
    def _is_transient_media_error(error: Exception) -> bool:
        message = str(error).lower()
        return isinstance(error, (TimeoutError, ConnectionError)) or any(marker in message for marker in (
            "timed out", "timeout", "temporarily", "connection", "429", "500", "502", "503", "504",
            "rate limit", "try again",
        ))

    def _media_sender(self) -> object:
        return (
            getattr(self, "media_client", None)
            or getattr(self, "send_client", None)
            or getattr(self, "client", None)
        )

    def _send_media_with_retry(self, operation: Callable[[object], object], label: str) -> object:
        """Retry transient uploads without blocking inbound polling or realtime."""
        lock = getattr(self, "media_send_lock", self.api_lock)
        for attempt in range(3):
            try:
                with lock:
                    return operation(self._media_sender())
            except Exception as error:
                if attempt == 2 or not self._is_transient_media_error(error):
                    raise
                delay = 2 ** attempt
                LOGGER.warning("Transient %s send failure (%s); retrying in %ds", label, error, delay)
                time.sleep(delay)
        raise RuntimeError(f"{label} send failed")

    def _pause_for_verification(self, error: object) -> None:
        mark_manual_verification(error)
        LOGGER.critical(
            "Instagram requires manual verification; network activity is paused. "
            "Run ./run.sh --browser-login and close Chromium after approval."
        )
        self.stop()

    def login(self) -> None:
        from lib.chromium_bridge import ChromiumBridge

        bridge = ChromiumBridge()
        session_id = bridge.saved_session_id()
        if session_id:
            try:
                if not self.client.login_by_sessionid(session_id):
                    raise RuntimeError("Instagram rejected the browser session")
                self.save_session()
                self._prepare_send_client()
                LOGGER.info("Restored the Chromium-backed Instagram session")
                return
            except Exception as error:
                LOGGER.warning("Saved Chromium session was rejected: %s", error)

        LOGGER.info("Opening Chromium for Instagram authentication")
        session_id = bridge.login(force_refresh=True)
        self.client.login_by_sessionid(session_id)
        self.save_session()
        self._prepare_send_client()
        LOGGER.info("Chromium session bridged to instagrapi")

    def _prepare_send_client(self) -> None:
        from instagrapi import Client
        client_settings = self.client.get_settings()

        sender = Client()
        sender.set_settings(client_settings)
        sender.delay_range = [0.5, 1.0]
        if config.PROXY_URL:
            sender.set_proxy(config.PROXY_URL)
        self.send_client = sender

        media_sender = Client()
        media_sender.set_settings(client_settings)
        media_sender.delay_range = [0.5, 1.0]
        if config.PROXY_URL:
            media_sender.set_proxy(config.PROXY_URL)
        self.media_client = media_sender

    def save_session(self) -> None:
        settings.SESSION_DIR.mkdir(parents=True, exist_ok=True)
        self.client.dump_settings(str(settings.INSTAGRAPI_SESSION_FILE))
        settings.INSTAGRAPI_SESSION_FILE.chmod(0o600)

    def _username(self, user_id: str, thread: object) -> str:
        key = str(user_id)
        with self.cache_lock:
            cached = self.username_cache.get(key)
            if cached and cached != "unknown":
                return cached
        for user in getattr(thread, "users", []) or []:
            candidate_id = getattr(user, "pk", getattr(user, "id", ""))
            if str(candidate_id) == key:
                username = getattr(user, "username", None)
                if username and username != "unknown":
                    with self.cache_lock:
                        if len(self.username_cache) >= 2000:
                            oldest = next(iter(self.username_cache))
                            self.username_cache.pop(oldest, None)
                        self.username_cache[key] = username
                    return username
        return "unknown"

    def _send_confirmed_text(self, thread_id: int, text: str) -> None:
        """Force URL-bearing replies through text; Instagram's link endpoint rejects this session."""
        client = getattr(self, "send_client", None) or self.client
        if "http" not in text:
            client.direct_answer(thread_id, text)
            return
        token = client.generate_mutation_token()
        payload = {
            "action": "send_item", "is_x_transport_forward": "false", "send_silently": "false",
            "is_shh_mode": "0", "send_attribution": "direct", "client_context": token,
            "device_id": client.android_device_id, "mutation_token": token, "_uuid": client.uuid,
            "btt_dual_send": "false", "is_ae_dual_send": "false", "offline_threading_id": token,
            "text": text, "thread_ids": json.dumps([int(thread_id)]),
        }
        client.private_request(
            "direct_v2/threads/broadcast/text/",
            data=client.with_default_data(payload), with_signature=False,
        )

    def _answer(self, thread_id: int, text: str) -> None:
        # Confirm delivery through REST and split every long response instead of truncating it.
        remaining = str(text)
        chunks: list[str] = []
        while remaining:
            if len(remaining) <= 1800:
                chunks.append(remaining)
                break
            split_at = remaining.rfind("\n", 0, 1800)
            if split_at < 900:
                split_at = remaining.rfind(" ", 0, 1800)
            if split_at < 900:
                split_at = 1800
            chunks.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()
        with getattr(self, "send_lock", self.api_lock):
            for chunk in chunks or [""]:
                self._send_confirmed_text(thread_id, chunk)

    def _mark_seen(self, thread_id: int, item_id: str) -> None:
        realtime = getattr(self.client, "realtime", None)
        if realtime is not None and getattr(realtime, "connected", False):
            try:
                with self.realtime_send_lock:
                    realtime.direct_mark_seen(thread_id, item_id)
                return
            except Exception as error:
                LOGGER.debug("Realtime mark-seen failed; using REST fallback: %s", error)
        with self.api_lock:
            self.client.direct_send_seen(thread_id)

    def _send_song(self, thread_id: int, request: SongRequest) -> None:
        cleaned_query = clean_media_query(request.query)
        if not cleaned_query:
            self._answer(thread_id, "❌ Please provide a song name or YouTube link.")
            return

        norm_key = (thread_id, "song", cleaned_query.lower())
        now = time.time()

        with self.media_request_lock:
            if norm_key in self.in_flight_media:
                if now - self.in_flight_media[norm_key] < 120.0:
                    LOGGER.info("Ignoring duplicate in-flight song '%s' for thread %s", cleaned_query, thread_id)
                    self._answer(thread_id, f"⏳ \"{cleaned_query}\" is already being downloaded! Hang tight...")
                    return

            if norm_key in self.recent_media:
                if now - self.recent_media[norm_key] < 20.0:
                    LOGGER.info("Ignoring duplicate recent song '%s' for thread %s", cleaned_query, thread_id)
                    self._answer(thread_id, f"⚡ \"{cleaned_query}\" was just sent in this chat!")
                    return

            self.in_flight_media[norm_key] = now

        download = None
        try:
            is_cached = self.song_service.is_cached(cleaned_query)
            if not is_cached:
                self._answer(thread_id, f"🔎 Downloading voice message: {cleaned_query[:100]}")

            download = self.song_service.download(cleaned_query)
            self._send_media_with_retry(
                lambda sender: sender.direct_send_voice(download.path, thread_ids=[thread_id]),
                "voice",
            )
            speed = "⚡ Cached" if download.cache_hit else "✅ Downloaded"
            self._answer(thread_id, f"{speed} song sent as voice: {download.title[:120]}")
        except Exception as error:
            LOGGER.warning("Song download/send failed: %s", error)
            self._answer(thread_id, f"❌ Song failed: {str(error)[:180]}")
        finally:
            with self.media_request_lock:
                self.in_flight_media.pop(norm_key, None)
                self.recent_media[norm_key] = time.time()
                cutoff = time.time() - 300.0
                self.recent_media = {k: v for k, v in self.recent_media.items() if v > cutoff}

            if download is not None:
                download.cleanup()

    def _send_video(self, thread_id: int, request: VideoRequest) -> None:
        cleaned_query = clean_media_query(request.query)
        if not cleaned_query:
            self._answer(thread_id, "❌ Please provide a video name or YouTube link.")
            return

        norm_key = (thread_id, "video", cleaned_query.lower())
        now = time.time()

        with self.media_request_lock:
            if norm_key in self.in_flight_media:
                if now - self.in_flight_media[norm_key] < 120.0:
                    LOGGER.info("Ignoring duplicate in-flight video '%s' for thread %s", cleaned_query, thread_id)
                    self._answer(thread_id, f"⏳ \"{cleaned_query}\" video is already downloading! Hang tight...")
                    return

            if norm_key in self.recent_media:
                if now - self.recent_media[norm_key] < 20.0:
                    LOGGER.info("Ignoring duplicate recent video '%s' for thread %s", cleaned_query, thread_id)
                    self._answer(thread_id, f"⚡ \"{cleaned_query}\" was just sent in this chat!")
                    return

            self.in_flight_media[norm_key] = now

        download = None
        try:
            is_cached = self.video_service.is_cached(cleaned_query)
            if not is_cached:
                self._answer(thread_id, f"🔎 Downloading video: {cleaned_query[:100]}")

            download = self.video_service.download(cleaned_query)
            self._send_media_with_retry(
                lambda sender: sender.direct_send_video(download.path, thread_ids=[thread_id]),
                "video",
            )
            speed = "⚡ Cached" if download.cache_hit else "✅ Downloaded"
            self._answer(thread_id, f"{speed}: {download.title[:120]}\n🔊 Open the video and tap the volume button to hear audio.")
        except Exception as error:
            LOGGER.warning("Video download/send failed: %s", error)
            self._answer(thread_id, f"❌ Video failed: {str(error)[:180]}")
        finally:
            with self.media_request_lock:
                self.in_flight_media.pop(norm_key, None)
                self.recent_media[norm_key] = time.time()
                cutoff = time.time() - 300.0
                self.recent_media = {k: v for k, v in self.recent_media.items() if v > cutoff}

            if download is not None:
                download.cleanup()

    def _send_pies(self, thread_id: int, request: PiesRequest) -> None:
        self._answer(thread_id, f"🔎 Fetching a fresh {request.country} photo…")
        image = None
        try:
            image = self.pies_service.fetch(request.country)
            self._send_media_with_retry(
                lambda sender: sender.direct_send_photo(image.path, thread_ids=[thread_id]),
                "photo",
            )
            speed = "⚡ Cached" if image.cache_hit else "✅ Downloaded"
            self._answer(thread_id, f"{speed} PIES: {request.country}")
        except Exception as error:
            LOGGER.warning("PIES photo failed: %s", error)
            self._answer(thread_id, f"❌ PIES failed: {str(error)[:150]}")
        finally:
            if image is not None:
                image.cleanup()

    def _send_sticker(self, thread_id: int, request: StickerRequest) -> None:
        self._answer(thread_id, "🎨 Making an Ineffa anime sticker…")
        try:
            sticker = self.sticker_service.render(request.mood)
            self._send_media_with_retry(
                lambda sender: sender.direct_send_photo(sticker.path, thread_ids=[thread_id]),
                "sticker",
            )
            speed = "⚡ Cached" if sticker.cache_hit else "✨ Created"
            self._answer(thread_id, f"{speed} anime sticker: {sticker.mood}")
        except Exception as error:
            LOGGER.warning("Anime sticker failed: %s", error)
            self._answer(thread_id, f"❌ Sticker failed: {str(error)[:150]}")

    def _send_lyrics(self, thread_id: int, request: LyricsRequest) -> None:
        cleaned_query = clean_media_query(request.query)
        if not cleaned_query:
            self._answer(thread_id, "❌ Please provide a song name.")
            return
        self._answer(thread_id, f"🔎 Finding lyrics: {cleaned_query[:100]}")
        try:
            result = self.lyrics_service.fetch(cleaned_query)
            chunks = result.chunks()
            if not chunks:
                raise LookupError("No lyrics found for that song")
            speed = "⚡ Cached" if result.cache_hit else "✅ Found"
            self._answer(thread_id, f"{speed}: {result.title} — {result.artist}\n\n{chunks[0]}")
            for chunk in chunks[1:]:
                self._answer(thread_id, chunk)
            if sum(len(chunk) for chunk in chunks) < len(result.lyrics):
                self._answer(thread_id, "…Lyrics truncated to keep Instagram delivery fast.")
        except Exception as error:
            LOGGER.warning("Lyrics lookup failed: %s", error)
            self._answer(thread_id, f"❌ Lyrics failed: {str(error)[:150]}")

    def _send_tts(self, thread_id: int, request: TTSRequest, username: str = "", sender_id: str = "", thread: object = None) -> None:
        can_run, reason = self._can_use_tts(str(thread_id), username, sender_id, thread=thread)
        if not can_run:
            self._answer(thread_id, reason)
            return
        self._answer(thread_id, f"🎙️ Generating voice note…")
        download = None
        try:
            download = self.tts_service.synthesize(request.text, request.lang)
            self._send_media_with_retry(
                lambda sender: sender.direct_send_voice(download.path, thread_ids=[thread_id]),
                "voice",
            )
        except Exception as error:
            LOGGER.warning("TTS send failed: %s", error)
            self._answer(thread_id, f"❌ Voice note failed: {str(error)[:180]}")
        finally:
            if download is not None:
                download.cleanup()

    def _send_search(self, thread_id: int, request: SearchRequest) -> None:
        result = self.search_service.search_web(request.query)
        self._answer(thread_id, result)

    def _send_wiki(self, thread_id: int, request: WikiRequest) -> None:
        result = self.search_service.search_wiki(request.topic)
        self._answer(thread_id, result)

    def _send_canvas(self, thread_id: int, request: CanvasRequest) -> None:
        self._answer(thread_id, f"🎨 Rendering image card…")
        download = None
        try:
            if request.kind == "meme":
                download = self.canvas_service.create_meme(request.text1, request.text2)
            else:
                download = self.canvas_service.create_quote_card(request.text1)
            self._send_media_with_retry(
                lambda sender: sender.direct_send_photo(download.path, thread_ids=[thread_id]),
                "photo",
            )
        except Exception as error:
            LOGGER.warning("Canvas send failed: %s", error)
            self._answer(thread_id, f"❌ Image rendering failed: {str(error)[:180]}")
        finally:
            if download is not None:
                download.cleanup()

    def _send_teach(self, thread_id: int, sender_id: str, username: str, request: TeachRequest) -> None:
        fact = request.fact.strip()
        parts = fact.split(" is ", 1) if " is " in fact else fact.split(":", 1) if ":" in fact else [fact[:30], fact]
        key = parts[0].strip()
        val = parts[1].strip() if len(parts) > 1 else fact
        self.database.teach_fact(sender_id, key, val)
        self.database.record_user_message(sender_id, username, f"Taught: {fact}")
        self._answer(thread_id, f"🧠 Saved to memory for @{username.lstrip('@')}: {fact}")

    def _send_github(self, thread_id: int, request: GitHubRequest) -> None:
        if request.kind == "projects":
            result = self.github_service.list_projects(request.target)
        else:
            result = self.github_service.get_repo_info(request.target)
        self._answer(thread_id, result)

    def _send_skull_reaction(self, thread_id_raw: int, message_id: str) -> None:
        try:
            with self.api_lock:
                msg_id_arg = int(str(message_id)) if str(message_id).isdigit() else str(message_id)
                self.client.direct_send_reaction(thread_id_raw, msg_id_arg, emoji="💀")
                LOGGER.info("Reacted 💀 to flagged message %s in thread %s", message_id, thread_id_raw)
        except Exception as error:
            LOGGER.warning("Failed to react 💀 to message %s: %s", message_id, error)
            try:
                self._answer(thread_id_raw, "💀 [Message Flagged by GC Monitor]")
            except Exception:
                pass

    def _send_dm_alert(self, target: str | int, message: str, photo_path: Path | None = None) -> None:
        try:
            target_str = str(target).strip()
            with self.api_lock:
                target_user_id: int | None = None
                if target_str.isdigit():
                    target_user_id = int(target_str)
                else:
                    clean_name = target_str.lower().lstrip("@")
                    if hasattr(self.database, "_connect"):
                        try:
                            with self.database._connect() as conn:
                                row = conn.execute("SELECT user_id FROM users WHERE LOWER(username) = ?", (clean_name,)).fetchone()
                                if row and str(row["user_id"]).isdigit():
                                    target_user_id = int(row["user_id"])
                        except Exception:
                            pass
                    if not target_user_id:
                        try:
                            target_user_id = int(self.client.user_id_from_username(clean_name))
                        except Exception as lookup_err:
                            LOGGER.warning("Failed to resolve user_id via REST for @%s: %s", clean_name, lookup_err)
                
                if target_user_id:
                    self.client.direct_send(message, user_ids=[target_user_id])
                    LOGGER.info("Successfully sent GC monitor DM alert text to recipient %s (ID: %s)", target, target_user_id)
                    if photo_path and Path(photo_path).exists():
                        try:
                            self.client.direct_send_photo(Path(photo_path), user_ids=[target_user_id])
                            LOGGER.info("Successfully sent GC monitor DM screenshot card to recipient %s (ID: %s)", target, target_user_id)
                        except Exception as photo_err:
                            LOGGER.warning("Failed to send screenshot evidence card to %s: %s", target, photo_err)
                else:
                    LOGGER.warning("Could not resolve user_id for recipient %s to send DM alert", target)
        except Exception as error:
            LOGGER.warning("Failed to send GC monitor DM alert to %s: %s", target, error)

    def _configure_gc_monitor(
        self,
        thread_id: str,
        arguments: list[str],
        admin: bool,
        thread: object = None,
        sender_id: str | None = None,
        username: str = "",
    ) -> str:
        if thread is not None and not getattr(thread, "is_group", False):
            return "🏰 .gcmonitor is built for Group Chats. In DMs, standard moderation applies."

        if arguments and arguments[0].lower() in {"rules", "rule"}:
            return self.gc_monitor.get_rules_overview()

        if not arguments or arguments[0].lower() in {"status", "check", "info", "help"}:
            settings = self.database.thread_settings(thread_id)
            current = settings.get("gc_monitor", False)
            mon_admin = settings.get("gc_monitor_admin_id", "")
            status = "ON 🛡️" if current else "OFF ❌"
            owner = config.OWNER_USERNAME
            recipient_str = f"@{owner.lstrip('@')}" + (f", User #{mon_admin}" if mon_admin else "")
            return (
                f"🏰 KNIGHTS OF FAVONIUS GC MONITOR STATUS\n\n"
                f"• Monitor Status: {status}\n"
                f"• Group Chat ID: {thread_id}\n"
                f"• Admin Alert DM Recipients: {recipient_str}\n\n"
                f"• Commands:\n"
                f"  - .gcmonitor on / off (Admins only)\n"
                f"  - .gcmonitor rules (View all 8 rules)\n"
                f"  - .gcmonitor test <text> (Test rule detection)\n"
                f"  - .gcmonitor status (Check current state)"
            )

        if arguments[0].lower() == "test":
            test_text = " ".join(arguments[1:]).strip()
            if not test_text:
                return "Usage: .gcmonitor test <sample message to evaluate>"
            group_title = str(getattr(thread, "thread_title", "Knights Of Favonius GC") or "Knights Of Favonius GC")
            violation = self.gc_monitor.check_message_ai(test_text, username or "test_user", group_name=group_title, ai_service=self.ai_service)
            if violation:
                preview = self.gc_monitor.format_admin_alert(violation)
                return f"🔍 [GC MONITOR TEST: VIOLATION DETECTED]\n\n{preview}"
            return "✅ [GC MONITOR TEST: CLEAN]\n\nNo rule violations detected in the provided message."

        is_owner = config.is_owner(username, sender_id)
        if not admin and not is_owner:
            return "only group admins can toggle .gcmonitor"

        target_state = arguments[0].lower()
        if target_state not in {"on", "off", "1", "0", "true", "false", "enable", "disable", "start", "stop"}:
            return "Usage: .gcmonitor on|off|status|rules|test <text>"
        enabled = target_state in {"on", "1", "true", "enable", "start"}
        self.database.set_thread_flag(thread_id, "gc_monitor", enabled, admin_id=sender_id)
        state_str = "ON 🛡️" if enabled else "OFF ❌"
        return f"🏰 Knights Of Favonius GC Monitor is now {state_str} for this group chat. Rule violations will trigger automatic DM alerts to group admins & owner."

    def _can_use_tts(self, thread_id: str, username: str, sender_id: str, thread: object = None) -> tuple[bool, str]:
        if config.is_owner(username, sender_id):
            return True, ""

        if self.database.is_banned(thread_id, sender_id, username):
            return False, f"🚫 @{username}, you are banned from using bot commands. Contact the bot owner (@jinshi_1) to get unbanned."

        if self.database.bot_setting("tts_global_enabled") == "0":
            return False, "🚫 TTS voice synthesis is currently disabled globally by the bot owner."

        is_group = bool(thread and getattr(thread, "is_group", False))
        if is_group:
            gc_enabled = self.database.thread_settings(thread_id).get("tts_enabled", True)
            if not gc_enabled:
                return False, "🚫 TTS voice synthesis is disabled in this group chat by admins."

        return True, ""

    def _configure_tts(
        self, thread_id: str, arguments: list[str], admin: bool, username: str, sender_id: str, thread: object = None
    ) -> str:
        is_owner = config.is_owner(username, sender_id)
        is_group = (thread is None) or bool(getattr(thread, "is_group", False))

        is_global_request = len(arguments) >= 2 and arguments[1].lower() in {"global", "all", "bot"}
        if len(arguments) >= 1 and arguments[0].lower() in {"globalon", "globaloff"}:
            is_global_request = True

        if is_global_request:
            if not is_owner:
                return "⛔ Only the bot owner can toggle global TTS settings."
            sub_cmd = arguments[0].lower()
            enable = sub_cmd in {"on", "globalon", "1", "enable"}
            self.database.set_bot_setting("tts_global_enabled", "1" if enable else "0")
            state_str = "ENABLED 🎙️" if enable else "DISABLED 🚫"
            return f"👑 Global TTS synthesis is now {state_str} across all chats."

        if not is_group:
            if not arguments or arguments[0].lower() in {"status", "check", "info"}:
                global_state = self.database.bot_setting("tts_global_enabled") != "0"
                return f"🎙️ TTS STATUS: Global System is {'ENABLED 🎙️' if global_state else 'DISABLED 🚫'}"
            return "🎙️ Use .ttson or .ttsoff inside group chats to configure group TTS settings."

        if not admin and not is_owner:
            return "only group admins can toggle group TTS settings"

        if not arguments or arguments[0].lower() in {"status", "check", "info"}:
            global_state = self.database.bot_setting("tts_global_enabled") != "0"
            gc_state = self.database.thread_settings(thread_id).get("tts_enabled", True)
            return (
                f"🎙️ TTS VOICE SYNTHESIS STATUS\n\n"
                f"• Global TTS: {'ENABLED 🎙️' if global_state else 'DISABLED 🚫'}\n"
                f"• This Group Chat TTS: {'ENABLED 🎙️' if gc_state else 'DISABLED 🚫'}\n\n"
                f"Admins: use .ttson OR .ttsoff for this group chat.\n"
                f"Owner: use .ttson global OR .ttsoff global for bot-wide toggle."
            )

        target_state = arguments[0].lower()
        if target_state in {"on", "ttson", "1", "enable"}:
            self.database.set_thread_flag(thread_id, "tts_enabled", True)
            return "🎙️ TTS voice synthesis is now ENABLED for this group chat."
        elif target_state in {"off", "ttsoff", "0", "disable"}:
            self.database.set_thread_flag(thread_id, "tts_enabled", False)
            return "🚫 TTS voice synthesis is now DISABLED for this group chat."
        return "Usage: .ttson OR .ttsoff OR .ttsstatus"

    @staticmethod
    def _ai_reply_target(sender_username: str, prompt: str) -> str:
        mentions = re.findall(r"@([A-Za-z0-9._]+)", prompt)
        bot_username = config.USERNAME.lower().lstrip("@")
        for mention in mentions:
            if mention.lower() != bot_username:
                return mention
        return sender_username.lstrip("@")

    def _configure_ai_autoreply_dm(
        self, arguments: list[str], username: str, sender_id: str
    ) -> str:
        current = self.database.bot_setting("ai_auto_reply_dm")
        if not self.owner_commands.is_owner(username, sender_id):
            return "⛔ Owner-only command."
        if not arguments or (len(arguments) == 1 and arguments[0].lower() == "status"):
            state = "DEFAULT" if current is None else current.upper()
            return f"🤖 Global DM auto-reply is {state}. Use .aiautoreplydm on/off."
        if len(arguments) != 1 or arguments[0].lower() not in {"on", "off"}:
            return "Usage: .aiautoreplydm on/off/status"
        state = arguments[0].lower()
        self.database.set_bot_setting("ai_auto_reply_dm", state)
        if state == "on":
            return "🤖 Global DM auto-reply is ON. Scanning pending DMs now."
        return "🤖 Global DM auto-reply is OFF for every DM."

    def _ai_autoreply_enabled(self, thread: object, thread_id: str) -> bool:
        per_thread = bool(self.database.thread_settings(thread_id)["ai_auto_reply"])
        if bool(getattr(thread, "is_group", False)):
            return per_thread
        global_mode = self.database.bot_setting("ai_auto_reply_dm")
        if global_mode is not None:
            return global_mode == "on"
        return per_thread

    def _configure_ai_autoreply_vn_dm(
        self, arguments: list[str], username: str, sender_id: str
    ) -> str:
        current = self.database.bot_setting("ai_auto_reply_vn_dm")
        if not self.owner_commands.is_owner(username, sender_id):
            return "⛔ Owner-only command."
        if not arguments or (len(arguments) == 1 and arguments[0].lower() == "status"):
            state = "DEFAULT" if current is None else current.upper()
            return f"🎙️ Global DM voice note auto-reply is {state}. Use .aiautoreplyvndm on/off."
        if len(arguments) != 1 or arguments[0].lower() not in {"on", "off"}:
            return "Usage: .aiautoreplyvndm on/off/status"
        state = arguments[0].lower()
        self.database.set_bot_setting("ai_auto_reply_vn_dm", state)
        if state == "on":
            return "🎙️ Global DM voice note auto-reply is ON. All DM replies will be sent as voice notes."
        return "🎙️ Global DM voice note auto-reply is OFF."

    def _ai_autoreply_vn_enabled(self, thread: object, thread_id: str) -> bool:
        if getattr(self, "database", None) is None or getattr(self, "tts_service", None) is None:
            return False
        per_thread = bool(self.database.thread_settings(thread_id).get("ai_auto_reply_vn", False))
        if bool(getattr(thread, "is_group", False)):
            return per_thread
        global_mode = self.database.bot_setting("ai_auto_reply_vn_dm")
        if global_mode is not None:
            return global_mode == "on"
        return per_thread

    def _configure_ai_autoreply_vn(
        self, thread_id: str, arguments: list[str], admin: bool, thread: object = None
    ) -> str:
        is_group = bool(getattr(thread, "is_group", False)) if thread is not None else not str(thread_id).startswith("dm")
        if not arguments or (len(arguments) == 1 and arguments[0].lower() == "status"):
            enabled = bool(self.database.thread_settings(thread_id).get("ai_auto_reply_vn", False))
            state = "ON" if enabled else "OFF"
            return f"🎙️ Voice note auto-reply is {state} for this chat. Use .aiautoreplyvn on/off."
        if len(arguments) != 1 or arguments[0].lower() not in {"on", "off"}:
            return "Usage: .aiautoreplyvn on/off/status"
        if is_group and not admin:
            return "Only the owner or a group admin can change voice note auto-reply."
        enabled = arguments[0].lower() == "on"
        self.database.set_thread_flag(thread_id, "ai_auto_reply_vn", enabled)
        state = "ON" if enabled else "OFF"
        return f"🎙️ Voice note auto-reply is now {state} for this chat."

    def _configure_ai_autoreply(
        self, thread_id: str, arguments: list[str], admin: bool, thread: object = None
    ) -> str:
        is_group = bool(getattr(thread, "is_group", False)) if thread is not None else not str(thread_id).startswith("dm")
        if not arguments or (len(arguments) == 1 and arguments[0].lower() == "status"):
            enabled = bool(self.database.thread_settings(thread_id)["ai_auto_reply"])
            state = "ON" if enabled else "OFF"
            return f"🤖 AI auto-reply is {state} for this chat. Use .aiautoreply on/off."
        if len(arguments) != 1 or arguments[0].lower() not in {"on", "off"}:
            return "Usage: .aiautoreply on/off/status"
        if is_group and not admin:
            return "Only the owner or a group admin can change AI auto-reply."
        enabled = arguments[0].lower() == "on"
        self.database.set_thread_flag(thread_id, "ai_auto_reply", enabled)
        state = "ON" if enabled else "OFF"
        return f"🤖 AI auto-reply is now {state} for this chat."

    @staticmethod
    def _should_ai_join_conversation(thread: object, text: str, message_id: str | None = None) -> bool:
        if not bool(getattr(thread, "is_group", False)):
            return True
        lowered = text.lower()
        bot_names = {
            settings.BOT_NAME.lower(),
            config.USERNAME.lower().lstrip("@"),
            config.OWNER_USERNAME.lower().lstrip("@"),
            "ineffa",
            "knight",
            "knightbot",
        } | {o.lower().lstrip("@") for o in getattr(config, "OWNER_USERNAMES", set()) if o}
        if any(re.search(rf"(?:^|[^a-z0-9_.])@?{re.escape(name)}(?:$|[^a-z0-9_.])", lowered) for name in bot_names if name):
            return True
        seed = str(message_id or text).encode("utf-8", errors="replace")
        score = int.from_bytes(hashlib.sha256(seed).digest()[:2], "big") % 100
        question = "?" in text or lowered.strip().startswith(("why ", "how ", "what ", "who ", "where ", "when ", "do u ", "did u ", "can u "))
        threshold = 68 if question else 32
        return score < threshold

    def _owner_in_group(self, thread: object) -> bool:
        """Return True if the bot owner (@jinshi_1) is a member of this group chat.

        DMs always return True — the gate only applies to group chats.
        """
        if not bool(getattr(thread, "is_group", False)):
            return True
        members = getattr(thread, "users", None) or []
        owner_names = {name.lower().lstrip("@") for name in config.OWNER_USERNAMES if name}
        owner_names.add(config.OWNER_USERNAME.lower().lstrip("@"))
        owner_ids = config.OWNER_USER_IDS
        for user in members:
            uname = str(getattr(user, "username", "") or "").lower().lstrip("@")
            uid = str(getattr(user, "pk", getattr(user, "id", "")))
            if uname in owner_names or uid in owner_ids:
                return True
        return False

    def _execute_message(self, thread: object, thread_id_raw: int, thread_id: str, sender_id: str, username: str, text: str, spam: bool = False, message_id: str | None = None) -> None:
        started = time.perf_counter()
        try:
            # ── OWNER-PRESENCE GATE ──────────────────────────────────────
            # All features require the owner (@jinshi_1) to be in the GC.
            # If the owner is not a member, the bot refuses to do anything.
            # Warning fires at most once per 10 seconds per GC (silent timer).
            if not self._owner_in_group(thread):
                if text.lstrip().startswith(settings.PREFIX):
                    now = time.monotonic()
                    last_warn = self.owner_gate_cooldown.get(thread_id, 0.0)
                    if now - last_warn >= 10.0:
                        self.owner_gate_cooldown[thread_id] = now
                        self._answer(
                            thread_id_raw,
                            "⛔ Bot features are disabled in this group chat.\n"
                            "The bot owner (@jinshi_1) must be a member of this GC for any commands to work.",
                        )
                return
            # ─────────────────────────────────────────────────────────────

            owner_result = self.owner_commands.handle(text, username, sender_id)
            if owner_result.handled:
                if owner_result.response:
                    self._answer(thread_id_raw, owner_result.response)
                if owner_result.home_alert:
                    _, alert_message = self.home_alert.trigger()
                    self._answer(thread_id_raw, alert_message)
                if owner_result.restart:
                    threading.Timer(1.0, lambda: os._exit(75)).start()
                return

            is_bot_owner = config.is_owner(username, sender_id)
            if hasattr(self.database, "get_ban_info"):
                ban_info = self.database.get_ban_info(thread_id, sender_id, username)
            elif hasattr(self.database, "is_banned"):
                try:
                    ban_info = {"banned": True, "banned_by": "admin"} if self.database.is_banned(thread_id, sender_id, username) else None
                except TypeError:
                    ban_info = {"banned": True, "banned_by": "admin"} if self.database.is_banned(thread_id, sender_id) else None
            else:
                ban_info = None

            if ban_info and not is_bot_owner:
                LOGGER.info("Banned user @%s (%s) attempted command/message in thread %s", username, sender_id, thread_id)
                now = time.time()
                user_key = f"{thread_id}:{sender_id or username}"
                last_notice = self.banned_user_cooldown.get(user_key, 0.0)
                if now - last_notice >= 10.0:
                    self.banned_user_cooldown[user_key] = now
                    self._answer(
                        thread_id_raw,
                        f"🚫 @{username}, you are banned from using bot commands. Contact the bot owner (@jinshi_1) to get unbanned.",
                    )
                return

            is_group = bool(thread and getattr(thread, "is_group", False))
            group_settings = self.database.thread_settings(thread_id)
            if hasattr(self.database, "record_user_message"):
                self.database.record_user_message(sender_id, username, text, thread_id=thread_id)

            if is_group and group_settings.get("gc_monitor"):
                group_title = str(getattr(thread, "thread_title", "Knights Of Favonius GC") or "Knights Of Favonius GC")
                violation = self.gc_monitor.check_message_ai(text, username, group_name=group_title, ai_service=self.ai_service)
                if violation:
                    alert_text = self.gc_monitor.format_admin_alert(violation)
                    gc_warning_text = self.gc_monitor.format_gc_warning(violation)
                    LOGGER.warning("GC MONITOR VIOLATION BY @%s: %s", username, alert_text)

                    if message_id:
                        threading.Thread(
                            target=self._send_skull_reaction,
                            args=(thread_id_raw, message_id),
                            daemon=True,
                        ).start()

                    try:
                        self.database.add_report(
                            thread_id=thread_id,
                            offender_id=sender_id,
                            offender_username=username,
                            rule_broken=violation.rule_broken,
                            reason=violation.reason,
                            snippet=violation.message_snippet,
                        )
                    except Exception as db_err:
                        LOGGER.warning("Failed to record GC violation in database: %s", db_err)

                    self._answer(thread_id_raw, gc_warning_text)

                    card_path: Path | None = None
                    try:
                        history_tuples = self.database.ai_thread_history(thread_id, limit=4)
                        card_path = self.gc_monitor.create_violation_screenshot(violation, recent_messages=history_tuples)
                    except Exception as card_err:
                        LOGGER.warning("Could not generate GC violation screenshot: %s", card_err)

                    recipients: set[str | int] = {config.OWNER_USERNAME}
                    for extra_owner in config.OWNER_USERNAMES:
                        if extra_owner:
                            recipients.add(extra_owner)
                    mon_admin = group_settings.get("gc_monitor_admin_id")
                    if mon_admin:
                        recipients.add(mon_admin)
                    if admin and sender_id:
                        recipients.add(sender_id)
                    admin_ids = getattr(thread, "admin_user_ids", None) or []
                    for aid in admin_ids:
                        recipients.add(aid)

                    for recipient in recipients:
                        threading.Thread(
                            target=self._send_dm_alert,
                            args=(recipient, alert_text, card_path),
                            daemon=True,
                        ).start()

            moderation = self.moderator.inspect_content(text, thread, sender_id, username, spam=spam)
            if moderation.response:
                self._answer(thread_id_raw, moderation.response)
            if moderation.blocked:
                return

            parts = text.strip().removeprefix(settings.PREFIX).split()
            command = parts[0].lower().rstrip(",") if parts else ""
            if command in {"aiautoreply", "autoreply"}:
                self._answer(thread_id_raw, self._configure_ai_autoreply(thread_id, parts[1:], admin, thread=thread))
                return
            if command in {"aiautoreplyvn", "autoreplyvn"}:
                self._answer(thread_id_raw, self._configure_ai_autoreply_vn(thread_id, parts[1:], admin, thread=thread))
                return
            if command == "aiautoreplydm":
                owner = self.owner_commands.is_owner(username, sender_id)
                requested_on = len(parts) == 2 and parts[1].lower() == "on"
                self._answer(thread_id_raw, self._configure_ai_autoreply_dm(parts[1:], username, sender_id))
                if owner and requested_on:
                    threading.Thread(target=self._refresh_pending_dms, name="pending-dm-scan", daemon=True).start()
                return
            if command == "aiautoreplyvndm":
                self._answer(thread_id_raw, self._configure_ai_autoreply_vn_dm(parts[1:], username, sender_id))
                return
            if command in {"gcmonitor", "favoniusmonitor", "monitorgc"}:
                self._answer(thread_id_raw, self._configure_gc_monitor(thread_id, parts[1:], admin, thread=thread, sender_id=sender_id, username=username))
                return
            if command in {"ttson", "ttsoff", "ttsstatus"}:
                args = [command.removeprefix("tts")] + parts[1:] if command in {"ttson", "ttsoff"} else parts[1:]
                self._answer(thread_id_raw, self._configure_tts(thread_id, args, admin, username, sender_id, thread=thread))
                return
            if command == "tts" and len(parts) > 1 and parts[1].lower() in {"on", "off", "status", "global", "check"}:
                self._answer(thread_id_raw, self._configure_tts(thread_id, parts[1:], admin, username, sender_id, thread=thread))
                return
            if command in {"leaderboard", "top", "topusers", "rank", "topchatters"}:
                is_group_chat = bool(thread and getattr(thread, "is_group", False))
                target_thread = thread_id if is_group_chat else None
                top_list = self.database.top_users(thread_id=target_thread, limit=10)
                title = "🏆 GROUP CHATTER LEADERBOARD 🏆" if is_group_chat else "🏆 TOP CHATTER LEADERBOARD 🏆"
                if not top_list:
                    self._answer(thread_id_raw, f"{title}\n\nNo activity recorded for this chat yet.")
                else:
                    lines = [f"{idx+1}. @{uname} — {count} msgs" for idx, (uname, count) in enumerate(top_list)]
                    self._answer(thread_id_raw, f"{title}\n\n" + "\n".join(lines))
                return

            chrome_group_command = command in {"add", "kick", "remove", "rm", "ban"}
            refresh_group_command = command in {"setname"}
            if (chrome_group_command or refresh_group_command) and admin and len(parts) > 1:
                self._answer(thread_id_raw, f"⚡ Processing .{command}…")
            if refresh_group_command or (chrome_group_command and not self.moderator.bot_is_admin(thread)):
                with self.api_lock:
                    try:
                        fresh_thread = self.client.direct_thread(int(thread_id_raw), amount=1)
                        self._cache_thread(fresh_thread)
                        moderation = self.moderator.handle(text, fresh_thread, sender_id, username)
                    except Exception:
                        moderation = self.moderator.handle(text, thread, sender_id, username)
            else:
                moderation = self.moderator.handle(text, thread, sender_id, username)
            if moderation.handled:
                if moderation.response:
                    self._answer(thread_id_raw, moderation.response)
                return
            ai_auto_reply = self._ai_autoreply_enabled(thread, thread_id)
            if (group_settings["bot_muted"] or group_settings["admin_only"]) and not admin:
                return

            if command == "insult":
                raw_target = parts[1] if len(parts) > 1 else username
                target_name = raw_target.rstrip("/").rsplit("/", 1)[-1].lstrip("@").rstrip(",;:").lower()
                admin_ids = {str(item) for item in (getattr(thread, "admin_user_ids", None) or [])}
                protected = target_name == config.OWNER_USERNAME
                for member in (getattr(thread, "users", None) or []):
                    member_name = str(getattr(member, "username", "")).lower()
                    member_id = str(getattr(member, "pk", getattr(member, "id", "")))
                    if member_name == target_name and member_id in admin_ids:
                        protected = True
                        break
                if protected:
                    self._answer(thread_id_raw, "👑 Owners and group admins are protected from insults.")
                    return

            response = self.handler.response_for(text, username, sender_id, thread_id)
            if isinstance(response, AIRequest):
                detector = getattr(self.ai_service, "detect_intent", None)
                intent = detector(response.prompt) if detector else None
                if isinstance(intent, SongRequest):
                    self._send_song(thread_id_raw, intent)
                elif isinstance(intent, LyricsRequest):
                    self._send_lyrics(thread_id_raw, intent)
                elif isinstance(intent, StickerRequest):
                    self._send_sticker(thread_id_raw, intent)
                elif isinstance(intent, PiesRequest):
                    self._send_pies(thread_id_raw, intent)
                else:
                    answer = self.ai_service.reply(response.prompt, username, sender_id)
                    reply_target = self._ai_reply_target(username, response.prompt)
                    if self._ai_autoreply_vn_enabled(thread, thread_id):
                        self._send_tts(thread_id_raw, TTSRequest(text=answer))
                    else:
                        self._answer(thread_id_raw, f"@{reply_target} {answer}")
                LOGGER.info("Completed local Ineffa command for @%s", username)
            elif isinstance(response, SongRequest):
                self._send_song(thread_id_raw, response)
                LOGGER.info("Completed song command for @%s", username)
            elif isinstance(response, StickerRequest):
                self._send_sticker(thread_id_raw, response)
                LOGGER.info("Completed anime sticker command for @%s", username)
            elif isinstance(response, VideoRequest):
                self._send_video(thread_id_raw, response)
                LOGGER.info("Completed video command for @%s", username)
            elif isinstance(response, LyricsRequest):
                self._send_lyrics(thread_id_raw, response)
                LOGGER.info("Completed lyrics command for @%s", username)
            elif isinstance(response, PiesRequest):
                self._send_pies(thread_id_raw, response)
                LOGGER.info("Completed PIES command for @%s", username)
            elif isinstance(response, TTSRequest):
                self._send_tts(thread_id_raw, response, username=username, sender_id=sender_id, thread=thread)
                LOGGER.info("Completed TTS command for @%s", username)
            elif isinstance(response, SearchRequest):
                self._send_search(thread_id_raw, response)
                LOGGER.info("Completed Search command for @%s", username)
            elif isinstance(response, WikiRequest):
                self._send_wiki(thread_id_raw, response)
                LOGGER.info("Completed Wiki command for @%s", username)
            elif isinstance(response, CanvasRequest):
                self._send_canvas(thread_id_raw, response)
                LOGGER.info("Completed Canvas command for @%s", username)
            elif isinstance(response, TeachRequest):
                self._send_teach(thread_id_raw, sender_id, username, response)
                LOGGER.info("Completed Teach command for @%s", username)
            elif isinstance(response, GitHubRequest):
                self._send_github(thread_id_raw, response)
                LOGGER.info("Completed GitHub command for @%s", username)
            elif response:
                self._answer(thread_id_raw, response)
                LOGGER.info("Replied to @%s", username)
            elif ai_auto_reply and text.strip() and not text.lstrip().startswith(settings.PREFIX):
                if not self._should_ai_join_conversation(thread, text, message_id):
                    LOGGER.info("Ineffa stayed quiet for @%s to keep group chat natural", username)
                    return
                context = self.database.ai_thread_history(thread_id, limit=7)
                for index in range(len(context) - 1, -1, -1):
                    context_username, context_message = context[index]
                    if context_username.lower().lstrip("@") == username.lower().lstrip("@") and context_message == " ".join(text.split())[:500]:
                        context.pop(index)
                        break
                chat_type = "group" if bool(getattr(thread, "is_group", False)) else "dm"
                detector = getattr(self.ai_service, "detect_intent", None)
                intent = detector(text) if detector else None
                if isinstance(intent, SongRequest):
                    self._send_song(thread_id_raw, intent)
                elif isinstance(intent, LyricsRequest):
                    self._send_lyrics(thread_id_raw, intent)
                elif isinstance(intent, StickerRequest):
                    self._send_sticker(thread_id_raw, intent)
                elif isinstance(intent, PiesRequest):
                    self._send_pies(thread_id_raw, intent)
                else:
                    answer = self.ai_service.reply(
                        text, username, sender_id, conversation_context=context[-6:], chat_type=chat_type
                    )
                    self.database.remember_thread_message(thread_id, str(self.client.user_id), settings.BOT_NAME, answer)
                    reply_target = self._ai_reply_target(username, text)
                    if self._ai_autoreply_vn_enabled(thread, thread_id):
                        self._send_tts(thread_id_raw, TTSRequest(text=answer))
                    else:
                        self._answer(thread_id_raw, f"@{reply_target} {answer}")
                LOGGER.info("Completed automatic Ineffa reply for @%s", username)
        except Exception as error:
            LOGGER.exception("Request from @%s failed", username)
            try:
                self._answer(thread_id_raw, f"❌ Request failed safely: {str(error)[:140]}")
            except Exception:
                LOGGER.exception("Could not send isolated error response")
        finally:
            if message_id:
                self.database.complete_message(message_id)
            LOGGER.info("Processed request from @%s in %.0f ms", username, (time.perf_counter() - started) * 1000)

    @staticmethod
    def _should_process_message(sender_id: str, own_id: str, text: str) -> bool:
        """Accept other users and explicitly enabled self-authored commands only."""
        if sender_id != own_id:
            return True
        return config.ALLOW_SELF_COMMANDS and text.lstrip().startswith(settings.PREFIX)

    def _process_thread(self, thread: object) -> None:
        raw_thread_id = getattr(thread, "id", getattr(thread, "thread_id", None))
        if raw_thread_id is None:
            return
        thread_id = str(raw_thread_id)
        messages = list(getattr(thread, "messages", None) or [])
        if not messages:
            with self.api_lock:
                messages = self.client.direct_messages(raw_thread_id, amount=20)
        def _msg_ts(item: object) -> float:
            ts = getattr(item, "timestamp", 0)
            if isinstance(ts, (int, float)):
                return float(ts) if ts < 1e11 else float(ts) / 1000.0
            return 0.0

        messages = sorted(messages, key=_msg_ts)
        own_id = str(self.client.user_id)
        ai_auto_reply = self._ai_autoreply_enabled(thread, thread_id)

        for message in messages:
            message_id = str(getattr(message, "id", ""))
            sender_id = str(getattr(message, "user_id", ""))
            if not message_id or not sender_id:
                continue
            text = getattr(message, "text", "") or ""
            if not self._should_process_message(sender_id, own_id, text):
                continue
            username = self._username(sender_id, thread)
            is_command = text.lstrip().startswith(settings.PREFIX)
            spam = False
            if not is_command:
                spam = self.moderator.is_spam(
                    message_id, text, thread, sender_id, username, getattr(message, "timestamp", None)
                )
                should_review = self.moderator.should_review_content(text, thread, sender_id, username)
                gc_mon_active = bool(self.database.thread_settings(thread_id).get("gc_monitor"))
                if not spam and not should_review and not gc_mon_active and not (ai_auto_reply and text.strip()):
                    continue
            if not self.database.claim_message(message_id, thread_id):
                continue

            if ai_auto_reply and not is_command and text.strip():
                self.database.remember_thread_message(thread_id, sender_id, username, text)
            admin = self.moderator.is_admin(thread, sender_id, username)
            LOGGER.info("Queueing request from @%s (admin=%s, group=%s)", username, admin, bool(getattr(thread, "is_group", False)))

            callback = lambda t=thread, rid=raw_thread_id, tid=thread_id, sid=sender_id, user=username, body=text, flagged_spam=spam, mid=message_id: self._execute_message(t, rid, tid, sid, user, body, flagged_spam, mid)
            try:
                receipt = self.jobs.submit(callback, admin=admin)
                if receipt.memory_pressure:
                    self._answer(raw_thread_id, f"⏳ Queued #{receipt.number} (memory pressure); admins receive priority.")
            except queue.Full:
                self.database.unclaim_message(message_id)
                self._answer(raw_thread_id, "⚠️ Request queue is full. Please retry shortly; request will be re-evaluated on next pass.")

        if messages:
            latest_item_id = str(getattr(messages[-1], "id", ""))
            if latest_item_id:
                self._mark_seen(raw_thread_id, latest_item_id)

    def poll_once(self, include_general: bool = False) -> int:
        self.poll_number += 1
        boxes = ["primary"]
        if include_general or self.poll_number == 1 or self.poll_number % 2 == 0:
            boxes.append("general")

        processed_threads: set[str] = set()
        for box in boxes:
            try:
                with self.api_lock:
                    threads = self.client.direct_threads(
                        amount=20,
                        selected_filter="unread",
                        box=box,
                        thread_message_limit=20,
                    )
            except Exception as error:
                if "login_required" in str(error).lower():
                    raise
                LOGGER.warning("Could not poll the %s inbox: %s", box, error)
                continue
            for thread in threads:
                thread_id = getattr(thread, "id", getattr(thread, "thread_id", None))
                key = str(thread_id)
                if thread_id is None or key in processed_threads:
                    continue
                processed_threads.add(key)
                self._cache_thread(thread)
                self._process_thread(thread)
        return len(processed_threads)

    def _cache_thread(self, thread: object) -> None:
        thread_id = getattr(thread, "id", getattr(thread, "thread_id", None))
        if thread_id is not None:
            lock = getattr(self, "cache_lock", None)
            if lock:
                with lock:
                    if len(self.thread_cache) >= 500:
                        oldest = next(iter(self.thread_cache))
                        self.thread_cache.pop(oldest, None)
                    self.thread_cache[str(thread_id)] = (time.monotonic(), thread)
            else:
                if not hasattr(self, "thread_cache"):
                    self.thread_cache = {}
                self.thread_cache[str(thread_id)] = (time.monotonic(), thread)

    def _cached_thread(self, thread_id: str) -> object | None:
        lock = getattr(self, "cache_lock", None)
        if lock:
            with lock:
                cached = self.thread_cache.get(str(thread_id))
                if cached is None:
                    return None
                cached_at, thread = cached
                if time.monotonic() - cached_at > self.thread_cache_ttl:
                    self.thread_cache.pop(str(thread_id), None)
                    return None
                return thread
        cached = getattr(self, "thread_cache", {}).get(str(thread_id))
        if cached is None:
            return None
        cached_at, thread = cached
        if time.monotonic() - cached_at > getattr(self, "thread_cache_ttl", 120.0):
            getattr(self, "thread_cache", {}).pop(str(thread_id), None)
            return None
        return thread

    @staticmethod
    def _realtime_message_from_payload(payload: object) -> tuple[str, object] | None:
        if not isinstance(payload, dict):
            return None
        outer = payload.get("message") if isinstance(payload.get("message"), dict) else payload
        thread_id = outer.get("thread_id")
        path = outer.get("path")
        if thread_id is None and isinstance(path, str):
            parts = path.strip("/").split("/")
            if "threads" in parts:
                index = parts.index("threads") + 1
                if index < len(parts):
                    thread_id = parts[index]
        if thread_id is None or not str(thread_id).isdigit():
            return None

        stack = [outer]
        message_data = None
        while stack:
            value = stack.pop()
            if not isinstance(value, dict):
                continue
            if "text" in value and ("user_id" in value or "sender_id" in value):
                message_data = value
                break
            stack.extend(item for item in value.values() if isinstance(item, dict))
        if message_data is None or not isinstance(message_data.get("text"), str):
            return None

        item_id = message_data.get("item_id", message_data.get("id"))
        if item_id is None and isinstance(path, str) and "/items/" in path:
            item_id = path.split("/items/", 1)[1].split("/", 1)[0]
        sender_id = message_data.get("user_id", message_data.get("sender_id"))
        if item_id is None or sender_id is None:
            return None
        message = SimpleNamespace(
            id=str(item_id),
            user_id=str(sender_id),
            text=message_data.get("text", ""),
            timestamp=message_data.get("timestamp", 0),
        )
        return str(thread_id), message

    @staticmethod
    def _thread_ids_from_payload(payload: object) -> set[str]:
        found: set[str] = set()
        stack = [payload]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "thread_id" and str(item).isdigit():
                        found.add(str(item))
                    elif key == "path" and isinstance(item, str):
                        parts = item.strip("/").split("/")
                        for marker in ("threads",):
                            if marker in parts:
                                index = parts.index(marker) + 1
                                if index < len(parts) and parts[index].isdigit():
                                    found.add(parts[index])
                    if isinstance(item, (dict, list, tuple)):
                        stack.append(item)
            elif isinstance(value, (list, tuple)):
                stack.extend(value)
        return found

    def _on_realtime_message(self, payload: object) -> None:
        # Ignore typing, seen, and other thread events. Fetching a thread for each
        # non-message event creates a tight REST loop and increases challenge risk.
        parsed = self._realtime_message_from_payload(payload)
        if parsed is None:
            return
        thread_id, _ = parsed
        self.realtime_thread_ids.put((thread_id, payload))
        self.wakeup.set()

    def _process_realtime_threads(self) -> int:
        pending: dict[str, list[object]] = {}
        while True:
            try:
                thread_id, payload = self.realtime_thread_ids.get_nowait()
                pending.setdefault(thread_id, []).append(payload)
            except queue.Empty:
                break
        for thread_id, payloads in pending.items():
            cached = self._cached_thread(thread_id)
            parsed = [self._realtime_message_from_payload(payload) for payload in payloads]
            messages = [item[1] for item in parsed if item is not None and item[0] == thread_id]
            if cached is not None and len(messages) == len(payloads):
                thread = SimpleNamespace(
                    id=int(thread_id),
                    thread_id=int(thread_id),
                    messages=messages,
                    users=getattr(cached, "users", []) or [],
                    admin_user_ids=getattr(cached, "admin_user_ids", []) or [],
                    is_group=bool(getattr(cached, "is_group", False)),
                    thread_title=getattr(cached, "thread_title", None),
                )
                self._process_thread(thread)
                continue
            with self.api_lock:
                thread = self.client.direct_thread(int(thread_id), amount=5)
            self._cache_thread(thread)
            self._process_thread(thread)
        return len(pending)

    def _subscribe_realtime(self, realtime: object) -> int:
        # One compact inbox request both seeds IRIS and warms metadata for zero-REST routing.
        with self.api_lock:
            threads = self.client.direct_threads(amount=20, thread_message_limit=1)
            seq_id = self.client.last_json.get("seq_id")
            snapshot_at_ms = self.client.last_json.get("snapshot_at_ms")
            if seq_id is None or snapshot_at_ms is None:
                raise RuntimeError("Direct inbox did not return realtime sync state")
            send_lock = getattr(self, "realtime_send_lock", None)
            if send_lock:
                with send_lock:
                    realtime.iris_subscribe(seq_id=seq_id, snapshot_at_ms=snapshot_at_ms)
            else:
                realtime.iris_subscribe(seq_id=seq_id, snapshot_at_ms=snapshot_at_ms)
        for thread in threads:
            self._cache_thread(thread)
        return len(threads)

    def _refresh_pending_dms(self) -> int:
        if self.database.bot_setting("ai_auto_reply_dm") != "on":
            return 0
        try:
            with self.api_lock:
                threads = self.client.direct_pending_inbox(amount=50)
        except Exception as error:
            if "login_required" in str(error).lower():
                raise
            LOGGER.warning("Could not poll pending DMs: %s", error)
            return 0
        processed = 0
        for thread in threads:
            if bool(getattr(thread, "is_group", False)):
                continue
            self._cache_thread(thread)
            self._process_thread(thread)
            processed += 1
        if processed:
            LOGGER.info("Scanned %d pending DM thread(s)", processed)
        return processed

    def _refresh_active_threads(self, message_limit: int = 5) -> int:
        # Refresh metadata before its TTL expires and recover any missed realtime items.
        with self.api_lock:
            threads = self.client.direct_threads(amount=20, thread_message_limit=message_limit)
        for thread in threads:
            self._cache_thread(thread)
            self._process_thread(thread)
        return len(threads) + self._refresh_pending_dms()

    def _realtime_loop(self) -> None:
        failures = 0
        while self.running:
            try:
                self.client.realtime_on("message", self._on_realtime_message)
                realtime = self.client.realtime_connect()
                warmed = self._subscribe_realtime(realtime)
                failures = 0
                LOGGER.info("Instagram realtime listener connected, direct inbox subscribed, %d threads warmed", warmed)
                while self.running:
                    try:
                        self.client.realtime_read_once()
                    except Exception as error:
                        if "timed out" in str(error).lower() and self.running:
                            try:
                                send_lock = getattr(self, "realtime_send_lock", None)
                                if send_lock:
                                    with send_lock:
                                        self.client.realtime_ping()
                                else:
                                    self.client.realtime_ping()
                            except Exception as ping_err:
                                LOGGER.debug("Realtime ping failed: %s", ping_err)
                                raise
                            continue
                        raise
            except Exception as error:
                realtime = getattr(self.client, "realtime", None)
                if realtime is not None:
                    realtime.connected = False
                if not self.running:
                    return
                if requires_manual_verification(error):
                    self._pause_for_verification(error)
                    return
                LOGGER.warning("Realtime listener interrupted; reconnecting while polling remains active: %s", error)
                wakeup = getattr(self, "wakeup", None)
                if wakeup is not None:
                    wakeup.set()
                try:
                    self.client.realtime_disconnect()
                except Exception:
                    pass
                self.client.realtime = None
                failures += 1
                time.sleep(min(30, 2 ** min(failures - 1, 5)))

    def _start_realtime(self) -> None:
        self.realtime_thread = threading.Thread(target=self._realtime_loop, name="instagram-realtime", daemon=True)
        self.realtime_thread.start()

    def run(self) -> None:
        self.login()
        self.ai_service.warm_up()
        LOGGER.info("Ineffa AI providers ready; local fallback is warm")
        self.database.prune()
        self.running = True
        self.jobs.start()
        self._start_realtime()
        LOGGER.info(
            "%s listening as @%s; owner=@%s; workers=%d; queue=%d; RSS limit=%d MiB",
            settings.BOT_NAME, config.USERNAME, config.OWNER_USERNAME, config.COMMAND_WORKERS,
            config.COMMAND_QUEUE_MAX, config.MAX_RSS_MB,
        )
        try:
            self.poll_once(include_general=True)
            self._refresh_active_threads(message_limit=20)
        except Exception as error:
            if requires_manual_verification(error):
                self._pause_for_verification(error)
            else:
                LOGGER.exception("Initial inbox recovery poll failed")
        while self.running:
            try:
                if self._process_realtime_threads():
                    continue
                self.wakeup.clear()
                if not self.realtime_thread_ids.empty():
                    continue
                realtime = getattr(self.client, "realtime", None)
                poll_timeout = max(config.POLL_SECONDS, 120) if getattr(realtime, "connected", False) else config.POLL_SECONDS
                signaled = self.wakeup.wait(timeout=poll_timeout)
                if not self.running:
                    break
                if self._process_realtime_threads():
                    continue
                realtime = getattr(self.client, "realtime", None)
                if not signaled and getattr(realtime, "connected", False):
                    self._refresh_active_threads()
                else:
                    self.poll_once(include_general=True if signaled else self.poll_number % 2 == 0)
            except KeyboardInterrupt:
                break
            except Exception as error:
                if requires_manual_verification(error):
                    self._pause_for_verification(error)
                    break
                LOGGER.exception("Polling failed; retrying with backoff")
                time.sleep(15)
        self.stop()

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        self.wakeup.set()
        self.jobs.stop()
        try:
            self.client.realtime_disconnect()
        except Exception:
            pass
        try:
            self.save_session()
        except Exception:
            LOGGER.exception("Could not save Instagram session")
        LOGGER.info("%s stopped at RSS %.1f MiB", settings.BOT_NAME, rss_bytes() / 1024 / 1024)


def offline_check() -> None:
    config.validate_credentials()
    Database()
    MessageHandler()
    OwnerCommands(Database())
    import instagrapi  # noqa: F401
    import yt_dlp  # noqa: F401
    browsers = sorted((settings.BASE_DIR / ".browsers").glob("chromium-*/chrome-linux64/chrome"))
    if not browsers or not browsers[-1].is_file():
        raise RuntimeError("Chromium is not installed; run ./setup.sh")
    print(f"Offline check passed: {settings.BOT_NAME}, owner, queue, moderation, voice songs, instagrapi, and Chromium are ready.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="jinshi_mds Instagram bot")
    parser.add_argument("--check", action="store_true", help="validate without contacting Instagram")
    parser.add_argument("--browser-login", action="store_true", help="refresh the Chromium login profile")
    return parser.parse_args()


def main() -> int:
    settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(settings.LOG_DIR / "bot.log")],
    )
    args = parse_args()
    try:
        if args.check:
            offline_check()
            return 0
        if args.browser_login:
            from lib.chromium_bridge import ChromiumBridge
            ChromiumBridge().login(keep_open=True)
            settings.INSTAGRAM_CHALLENGE_FILE.unlink(missing_ok=True)
            print("Chromium Instagram profile and session saved; verification pause cleared.")
            return 0

        bot = JinshiMds()

        def terminate(*_: object) -> None:
            bot.stop()

        signal.signal(signal.SIGTERM, terminate)
        bot.run()
        return 0
    except Exception as error:
        if requires_manual_verification(error):
            mark_manual_verification(error)
            LOGGER.critical("Startup paused for manual Instagram verification: %s", error)
            return 78
        LOGGER.exception("Startup failed: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
