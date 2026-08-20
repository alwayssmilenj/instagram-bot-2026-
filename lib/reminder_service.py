"""Background scheduled reminder service for KnightBot."""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

from lib.database import Database


def parse_duration_seconds(duration_str: str) -> int | None:
    """Parse strings like '10s', '5m', '2h', '1d', '30mins' into total seconds."""
    clean = duration_str.strip().lower()
    match = re.match(r"^(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)$", clean)
    if not match:
        return None
    val = int(match.group(1))
    unit = match.group(2)
    if unit.startswith("s"):
        return val
    if unit.startswith("m"):
        return val * 60
    if unit.startswith("h"):
        return val * 3600
    if unit.startswith("d"):
        return val * 86400
    return None


@dataclass
class ReminderItem:
    id: int
    thread_id: str
    user_id: str
    username: str
    reminder_text: str
    remind_at: float
    created_at: str


class ReminderService:
    """Manages scheduled reminders stored in SQLite with background daemon dispatch."""

    def __init__(self, database: Database, dispatch_callback: Callable[[str, str], None] | None = None) -> None:
        self.database = database
        self.dispatch_callback = dispatch_callback
        self._running = False
        self._thread: threading.Thread | None = None
        self._init_db()

    def _init_db(self) -> None:
        try:
            with self.database._connect() as conn:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS reminders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        thread_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        username TEXT NOT NULL,
                        reminder_text TEXT NOT NULL,
                        remind_at REAL NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        status TEXT NOT NULL DEFAULT 'pending'
                    )"""
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_status_time ON reminders(status, remind_at)")
        except Exception:
            pass

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._worker_loop, name="reminder-daemon", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def add_reminder(self, thread_id: str, user_id: str, username: str, duration_str: str, text: str) -> tuple[bool, str]:
        seconds = parse_duration_seconds(duration_str)
        if not seconds:
            return False, "⚠️ Invalid duration. Examples: `10m`, `30s`, `2h`, `1d`."
        if seconds < 5:
            return False, "⚠️ Reminder duration must be at least 5 seconds."
        if seconds > 86400 * 30:
            return False, "⚠️ Reminders cannot be set further than 30 days in advance."
        
        remind_at = time.time() + seconds
        clean_text = text.strip() or "Reminder!"
        
        with self.database._connect() as conn:
            cur = conn.execute(
                "INSERT INTO reminders (thread_id, user_id, username, reminder_text, remind_at) VALUES (?, ?, ?, ?, ?)",
                (str(thread_id), str(user_id), username.lstrip("@"), clean_text[:500], remind_at),
            )
            rem_id = cur.lastrowid

        mins, rem_sec = divmod(seconds, 60)
        hours, mins = divmod(mins, 60)
        days, hours = divmod(hours, 24)
        
        parts = []
        if days > 0: parts.append(f"{days}d")
        if hours > 0: parts.append(f"{hours}h")
        if mins > 0: parts.append(f"{mins}m")
        if rem_sec > 0 and not days and not hours: parts.append(f"{rem_sec}s")
        time_display = " ".join(parts) or f"{seconds}s"

        return True, f"⏰ Reminder #{rem_id} set for @{username.lstrip('@')} in {time_display}: \"{clean_text}\""

    def get_user_reminders(self, user_id: str, username: str = "") -> list[ReminderItem]:
        with self.database._connect() as conn:
            rows = conn.execute(
                """SELECT id, thread_id, user_id, username, reminder_text, remind_at, created_at 
                   FROM reminders 
                   WHERE status = 'pending' AND (user_id = ? OR LOWER(username) = ?)
                   ORDER BY remind_at ASC LIMIT 10""",
                (str(user_id), username.lower().lstrip("@")),
            ).fetchall()
        return [ReminderItem(*r) for r in rows]

    def cancel_reminder(self, reminder_id: int, user_id: str, is_owner_or_admin: bool = False) -> tuple[bool, str]:
        with self.database._connect() as conn:
            row = conn.execute("SELECT user_id, username FROM reminders WHERE id = ? AND status = 'pending'", (reminder_id,)).fetchone()
            if not row:
                return False, f"⚠️ Active reminder #{reminder_id} not found."
            if not is_owner_or_admin and str(row[0]) != str(user_id):
                return False, "⛔ You can only cancel your own reminders."
            conn.execute("UPDATE reminders SET status = 'cancelled' WHERE id = ?", (reminder_id,))
        return True, f"✅ Reminder #{reminder_id} cancelled."

    def _worker_loop(self) -> None:
        while self._running:
            try:
                now = time.time()
                due: list[tuple[int, str, str, str, str]] = []
                with self.database._connect() as conn:
                    rows = conn.execute(
                        """SELECT id, thread_id, user_id, username, reminder_text 
                           FROM reminders 
                           WHERE status = 'pending' AND remind_at <= ?
                           LIMIT 20""",
                        (now,),
                    ).fetchall()
                    for r in rows:
                        due.append((r[0], r[1], r[2], r[3], r[4]))
                        conn.execute("UPDATE reminders SET status = 'completed' WHERE id = ?", (r[0],))

                for rem_id, thread_id, user_id, username, rem_text in due:
                    msg = f"⏰ **REMINDER ALERT** for @{username}!\n🔔 {rem_text}"
                    if self.dispatch_callback:
                        try:
                            self.dispatch_callback(thread_id, msg)
                        except Exception:
                            pass
            except Exception:
                pass
            time.sleep(4)
