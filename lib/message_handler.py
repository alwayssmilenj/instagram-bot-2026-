"""Incoming-message command dispatch with account-safe rate limiting."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

import config
import settings
from commands.core import CommandResponse, CommandRouter, MessageContext


class ReplyLimiter:
    """Thread-safe fixed-window limiter keyed by thread, user, or account with LRU bounds."""

    def __init__(self, maximum: int, window_seconds: int = 3600, max_keys: int = 2000) -> None:
        self.maximum = maximum
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self.events: dict[str, deque[float]] = {}
        self.lock = threading.Lock()
        self._last_prune = time.monotonic()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            if len(self.events) > self.max_keys and (now - self._last_prune > 30.0 or len(self.events) > self.max_keys * 1.25):
                self._last_prune = now
                stale = [k for k, q in self.events.items() if not q or q[-1] <= now - self.window_seconds]
                for k in stale:
                    self.events.pop(k, None)
                if len(self.events) > self.max_keys:
                    excess = len(self.events) - self.max_keys
                    for k in list(self.events.keys())[:excess]:
                        self.events.pop(k, None)

            events = self.events.setdefault(key, deque())
            while events and events[0] <= now - self.window_seconds:
                events.popleft()
            if len(events) >= self.maximum:
                return False
            events.append(now)
            return True


class CooldownLimiter:
    """Prevent burst replies from one sender without simulating human behavior, bounded memory."""

    def __init__(self, minimum_interval: float, max_keys: int = 2000) -> None:
        self.minimum_interval = max(0.0, float(minimum_interval))
        self.max_keys = max_keys
        self.last_allowed: dict[str, float] = {}
        self.lock = threading.Lock()
        self._last_prune = time.monotonic()

    def allow(self, key: str) -> bool:
        if self.minimum_interval <= 0:
            return True
        now = time.monotonic()
        with self.lock:
            if len(self.last_allowed) > self.max_keys and (now - self._last_prune > 30.0 or len(self.last_allowed) > self.max_keys * 1.25):
                self._last_prune = now
                stale = [k for k, ts in self.last_allowed.items() if now - ts > self.minimum_interval * 10]
                for k in stale:
                    self.last_allowed.pop(k, None)
                if len(self.last_allowed) > self.max_keys:
                    excess = len(self.last_allowed) - self.max_keys
                    for k in list(self.last_allowed.keys())[:excess]:
                        self.last_allowed.pop(k, None)

            previous = self.last_allowed.get(key)
            if previous is not None and now - previous < self.minimum_interval:
                return False
            self.last_allowed[key] = now
            return True


class MessageHandler:
    def __init__(self) -> None:
        self.router = CommandRouter()
        self.limiter = ReplyLimiter(config.MAX_REPLIES_PER_HOUR)
        self.global_limiter = ReplyLimiter(config.MAX_GLOBAL_REPLIES_PER_HOUR)
        self.essential_limiter = ReplyLimiter(12, window_seconds=300)
        self.essential_global_limiter = ReplyLimiter(60, window_seconds=300)
        self.cooldown = CooldownLimiter(config.MIN_REPLY_INTERVAL_SECONDS)

    def response_for(self, text: str, username: str, user_id: str, thread_id: str) -> CommandResponse:
        context = MessageContext(username=username, user_id=str(user_id), thread_id=str(thread_id))
        response = self.router.route(text, context)
        if response is None:
            return None
        command_raw = text.strip()
        for p in (getattr(settings, "PREFIX", "."), ".", ",", "!", "/"):
            if command_raw.startswith(p):
                command_raw = command_raw[len(p):]
        command = command_raw.split(maxsplit=1)[0].lower().rstrip(",:;.") if command_raw else ""
        if command in {"menu", "help", "commands", "ping", "alive", "owner", "id", "w", "debate", "debatewith"}:
            if not self.essential_limiter.allow(str(thread_id)):
                return None
            if not self.essential_global_limiter.allow("account"):
                return None
            return response
        if not self.cooldown.allow(str(user_id)):
            return None
        if not self.limiter.allow(str(thread_id)):
            return None
        if not self.global_limiter.allow("account"):
            return None
        return response
