"""Anti-Raid Shield & Burst Join Protection for KnightBot (Ineffa).

Monitors member join velocity, detects distributed raid attacks,
triggers automatic lockdowns, and generates quarantine alerts.
"""

from __future__ import annotations

import dataclasses
import enum
import logging
import threading
import time
from collections import defaultdict, deque
from typing import Any, Sequence

LOGGER = logging.getLogger("knightbot.antiraid")


class RaidMode(str, enum.Enum):
    LOCKDOWN = "lockdown"
    KICK_RAIDERS = "kick_raiders"
    ALERT_ONLY = "alert_only"


@dataclasses.dataclass(frozen=True)
class JoinEvent:
    user_id: str
    username: str
    timestamp: float


@dataclasses.dataclass(frozen=True)
class RaidAlert:
    thread_id: str
    triggered_at: float
    join_count: int
    window_seconds: float
    raiders: tuple[str, ...]
    mode: RaidMode
    alert_message: str


class AntiRaidShield:
    """Thread-safe Anti-Raid velocity tracker and automated shield."""

    def __init__(
        self,
        burst_threshold: int = 5,
        window_seconds: float = 10.0,
        cooldown_seconds: float = 60.0,
        default_mode: RaidMode = RaidMode.LOCKDOWN,
    ) -> None:
        self.burst_threshold = max(2, int(burst_threshold))
        self.window_seconds = max(1.0, float(window_seconds))
        self.cooldown_seconds = max(5.0, float(cooldown_seconds))
        self.default_mode = default_mode
        self._lock = threading.Lock()

        # thread_id -> deque of JoinEvent
        self._join_history: dict[str, deque[JoinEvent]] = defaultdict(deque)
        # thread_id -> raid start timestamp
        self._active_raids: dict[str, float] = {}
        # thread_id -> set of whitelisted user_ids
        self._whitelists: dict[str, set[str]] = defaultdict(set)
        # thread_id -> list of raider user_ids from latest raid
        self._raiders_logged: dict[str, list[str]] = defaultdict(list)
        # thread_id -> RaidMode
        self._thread_modes: dict[str, RaidMode] = {}

    def whitelist_user(self, thread_id: str, user_id: str) -> None:
        """Add a user to the thread's bypass whitelist."""
        with self._lock:
            self._whitelists[str(thread_id)].add(str(user_id))

    def is_whitelisted(self, thread_id: str, user_id: str) -> bool:
        """Check if user is whitelisted."""
        with self._lock:
            return str(user_id) in self._whitelists[str(thread_id)]

    def set_mode(self, thread_id: str, mode: RaidMode) -> None:
        """Configure mitigation mode for a thread."""
        with self._lock:
            self._thread_modes[str(thread_id)] = mode

    def get_mode(self, thread_id: str) -> RaidMode:
        with self._lock:
            return self._thread_modes.get(str(thread_id), self.default_mode)

    def record_join(
        self,
        thread_id: str | int,
        user_id: str | int,
        username: str = "",
        timestamp: float | None = None,
    ) -> RaidAlert | None:
        """Record a join event and evaluate if raid threshold is breached.

        Returns:
            RaidAlert if a raid is triggered/active, or None if under rate limit.
        """
        t_id = str(thread_id)
        u_id = str(user_id)
        u_name = str(username or u_id).lstrip("@")
        now = float(timestamp if timestamp is not None else time.time())

        with self._lock:
            # Whitelist bypass
            if u_id in self._whitelists[t_id]:
                return None

            # Clean expired events outside sliding window
            history = self._join_history[t_id]
            cutoff = now - self.window_seconds
            while history and history[0].timestamp < cutoff:
                history.popleft()

            # Ingest new join
            event = JoinEvent(user_id=u_id, username=u_name, timestamp=now)
            history.append(event)

            # Check if currently in active raid cooldown
            if t_id in self._active_raids:
                raid_start = self._active_raids[t_id]
                if now - raid_start < self.cooldown_seconds:
                    # Still in active raid cooldown; add to raiders list
                    if u_id not in self._raiders_logged[t_id]:
                        self._raiders_logged[t_id].append(u_id)
                    mode = self._thread_modes.get(t_id, self.default_mode)
                    raiders_tuple = tuple(self._raiders_logged[t_id])
                    alert_msg = self.format_raid_alert(t_id, len(history), self._raiders_logged[t_id])
                    return RaidAlert(
                        thread_id=t_id,
                        triggered_at=raid_start,
                        join_count=len(history),
                        window_seconds=self.window_seconds,
                        raiders=raiders_tuple,
                        mode=mode,
                        alert_message=alert_msg,
                    )
                else:
                    # Cooldown expired
                    self._active_raids.pop(t_id, None)

            # Check threshold breach
            if len(history) >= self.burst_threshold:
                self._active_raids[t_id] = now
                raiders = [e.user_id for e in history]
                self._raiders_logged[t_id] = list(raiders)
                mode = self._thread_modes.get(t_id, self.default_mode)
                alert_msg = self.format_raid_alert(t_id, len(history), raiders)
                LOGGER.warning("ANTI-RAID TRIGGERED in thread %s: %d joins in %.1fs", t_id, len(history), self.window_seconds)

                return RaidAlert(
                    thread_id=t_id,
                    triggered_at=now,
                    join_count=len(history),
                    window_seconds=self.window_seconds,
                    raiders=tuple(raiders),
                    mode=mode,
                    alert_message=alert_msg,
                )

            return None

    def is_raid_active(self, thread_id: str | int, now: float | None = None) -> bool:
        """Check if thread is currently locked down due to an active raid."""
        t_id = str(thread_id)
        current_time = float(now if now is not None else time.time())
        with self._lock:
            if t_id not in self._active_raids:
                return False

            raid_start = self._active_raids[t_id]
            if current_time - raid_start < self.cooldown_seconds:
                return True

            # Expired
            self._active_raids.pop(t_id, None)
            return False

    def get_active_raiders(self, thread_id: str | int) -> list[str]:
        """Get the list of user IDs flagged in the latest raid burst."""
        with self._lock:
            return list(self._raiders_logged.get(str(thread_id), []))

    def resolve_raid(self, thread_id: str | int) -> bool:
        """Manually lift raid lockdown and clear burst history."""
        t_id = str(thread_id)
        with self._lock:
            existed = t_id in self._active_raids
            self._active_raids.pop(t_id, None)
            self._join_history.pop(t_id, None)
            self._raiders_logged.pop(t_id, None)
            return existed

    def format_raid_alert(
        self,
        thread_id: str | int,
        join_count: int,
        raiders: Sequence[str],
        group_name: str = "Group Chat",
    ) -> str:
        """Generate formatted alert card for admins."""
        raider_preview = ", ".join(f"@{r}" for r in raiders[:8])
        if len(raiders) > 8:
            raider_preview += f" (+{len(raiders) - 8} more)"

        return (
            f"🚨 **ANTI-RAID SHIELD ACTIVATED** 🚨\n\n"
            f"🛡️ **Target**: {group_name} (`{thread_id}`)\n"
            f"⚡ **Velocity**: {join_count} joins within {self.window_seconds:.0f}s (Threshold: {self.burst_threshold})\n"
            f"🔒 **Status**: Group locked down for {self.cooldown_seconds:.0f}s cooldown\n"
            f"👥 **Flagged Raiders** ({len(raiders)}): {raider_preview}\n\n"
            f"⚠️ *Invite links should be reset immediately.*"
        )
