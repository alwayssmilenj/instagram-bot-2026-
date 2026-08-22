"""Background scheduled reminder service for KnightBot."""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

from lib.database import Database


def parse_duration_seconds(duration_str: str) -> int | None:
    """Parse duration strings like '10s', '5m', '2h', '1d', '1h30m', '1.5h', '2h 15m', 'in 10 mins' into total seconds."""
    clean = duration_str.strip().lower()
    if not clean:
        return None

    # Strip natural language prefixes like 'in ', 'after ', 'for '
    for prefix in ("in ", "after ", "for ", "every "):
        if clean.startswith(prefix):
            clean = clean[len(prefix):].strip()

    # 1. Check decimal format: e.g. '1.5h', '0.5m', '2.5d'
    dec_match = re.match(r"^(\d+(?:\.\d+)?)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)$", clean)
    if dec_match:
        val = float(dec_match.group(1))
        unit = dec_match.group(2)
        if unit.startswith("s"):
            return int(val)
        if unit.startswith("m"):
            return int(val * 60)
        if unit.startswith("h"):
            return int(val * 3600)
        if unit.startswith("d"):
            return int(val * 86400)

    # 2. Check compound / multi-part format: e.g. '1h30m', '1h 30m', '2 days 4 hours 10 mins', '10m 30s'
    pattern = re.compile(r"(\d+(?:\.\d+)?)\s*(d|days?|h|hrs?|hours?|m|mins?|minutes?|s|secs?|seconds?)")
    matches = pattern.findall(clean)
    if matches:
        total = 0.0
        for num_str, unit in matches:
            val = float(num_str)
            if unit.startswith("d"):
                total += val * 86400
            elif unit.startswith("h"):
                total += val * 3600
            elif unit.startswith("m"):
                total += val * 60
            elif unit.startswith("s"):
                total += val
        if total > 0:
            return int(total)

    # 3. Pure digit fallback (assumes minutes if > 0)
    if clean.isdigit():
        return int(clean) * 60

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
    target_username: str = ""


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
                        status TEXT NOT NULL DEFAULT 'pending',
                        target_username TEXT NOT NULL DEFAULT ''
                    )"""
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_status_time ON reminders(status, remind_at)")
                try:
                    conn.execute("ALTER TABLE reminders ADD COLUMN target_username TEXT NOT NULL DEFAULT ''")
                except Exception:
                    pass
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

    def add_reminder(
        self,
        thread_id: str,
        user_id: str,
        username: str,
        duration_str: str,
        text: str,
        target_username: str = "",
    ) -> tuple[bool, str]:
        seconds = parse_duration_seconds(duration_str)
        if not seconds:
            return False, "⚠️ Invalid duration. Examples: `10m`, `30s`, `1h30m`, `1.5h`, `2d 4h`."
        if seconds < 5:
            return False, "⚠️ Reminder duration must be at least 5 seconds."
        if seconds > 86400 * 30:
            return False, "⚠️ Reminders cannot be set further than 30 days in advance."

        # Check if text contains a target tag e.g. "@friend do homework"
        clean_text = text.strip() or "Reminder!"
        target_user = target_username.lstrip("@").strip()
        if not target_user and clean_text.startswith("@"):
            parts = clean_text.split(maxsplit=1)
            target_user = parts[0].lstrip("@")
            clean_text = parts[1] if len(parts) > 1 else "Reminder!"

        remind_at = time.time() + seconds

        with self.database._connect() as conn:
            cur = conn.execute(
                """INSERT INTO reminders (thread_id, user_id, username, reminder_text, remind_at, target_username)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (str(thread_id), str(user_id), username.lstrip("@"), clean_text[:500], remind_at, target_user),
            )
            rem_id = cur.lastrowid

        mins, rem_sec = divmod(seconds, 60)
        hours, mins = divmod(mins, 60)
        days, hours = divmod(hours, 24)

        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if mins > 0:
            parts.append(f"{mins}m")
        if rem_sec > 0 and not days and not hours:
            parts.append(f"{rem_sec}s")
        time_display = " ".join(parts) or f"{seconds}s"

        user_tag = target_user if target_user else username.lstrip("@")
        return True, f"⏰ Reminder #{rem_id} set for @{user_tag} in {time_display}: \"{clean_text}\""

    def get_user_reminders(self, user_id: str, username: str = "") -> list[ReminderItem]:
        with self.database._connect() as conn:
            rows = conn.execute(
                """SELECT id, thread_id, user_id, username, reminder_text, remind_at, created_at,
                          COALESCE(target_username, '') as target_username
                   FROM reminders 
                   WHERE status = 'pending' AND (user_id = ? OR LOWER(username) = ? OR LOWER(target_username) = ?)
                   ORDER BY remind_at ASC LIMIT 15""",
                (str(user_id), username.lower().lstrip("@"), username.lower().lstrip("@")),
            ).fetchall()
        return [
            ReminderItem(
                id=r["id"],
                thread_id=str(r["thread_id"]),
                user_id=str(r["user_id"]),
                username=str(r["username"]),
                reminder_text=str(r["reminder_text"]),
                remind_at=float(r["remind_at"]),
                created_at=str(r["created_at"]),
                target_username=str(r["target_username"]),
            )
            for r in rows
        ]

    def cancel_reminder(self, reminder_id: int, user_id: str, is_owner_or_admin: bool = False) -> tuple[bool, str]:
        with self.database._connect() as conn:
            row = conn.execute(
                "SELECT user_id, username, reminder_text FROM reminders WHERE id = ? AND status = 'pending'",
                (reminder_id,),
            ).fetchone()
            if not row:
                return False, f"⚠️ Active reminder #{reminder_id} not found."
            if not is_owner_or_admin and str(row["user_id"]) != str(user_id):
                return False, "⛔ You can only cancel your own reminders."
            conn.execute("UPDATE reminders SET status = 'cancelled' WHERE id = ?", (reminder_id,))
        return True, f"✅ Reminder #{reminder_id} (\"{row['reminder_text'][:30]}\") has been cancelled."

    def snooze_reminder(self, reminder_id: int, user_id: str, duration_str: str = "10m") -> tuple[bool, str]:
        seconds = parse_duration_seconds(duration_str) or 600
        with self.database._connect() as conn:
            row = conn.execute(
                "SELECT user_id, username, reminder_text FROM reminders WHERE id = ?",
                (reminder_id,),
            ).fetchone()
            if not row:
                return False, f"⚠️ Reminder #{reminder_id} not found."
            new_remind_at = time.time() + seconds
            conn.execute(
                "UPDATE reminders SET status = 'pending', remind_at = ? WHERE id = ?",
                (new_remind_at, reminder_id),
            )
        mins = seconds // 60
        return True, f"💤 Reminder #{reminder_id} snoozed for {mins}m."

    def clear_all_reminders(self, user_id: str) -> tuple[bool, str]:
        with self.database._connect() as conn:
            cur = conn.execute("UPDATE reminders SET status = 'cancelled' WHERE user_id = ? AND status = 'pending'", (str(user_id),))
            count = cur.rowcount
        return True, f"🧹 Cleared {count} pending reminder(s)."

    def _worker_loop(self) -> None:
        while self._running:
            try:
                now = time.time()
                due: list[tuple[int, str, str, str, str, str]] = []
                with self.database._connect() as conn:
                    rows = conn.execute(
                        """SELECT id, thread_id, user_id, username, reminder_text, COALESCE(target_username, '') as target_username
                           FROM reminders 
                           WHERE status = 'pending' AND remind_at <= ?
                           LIMIT 20""",
                        (now,),
                    ).fetchall()
                    for r in rows:
                        due.append((r["id"], str(r["thread_id"]), str(r["user_id"]), str(r["username"]), str(r["reminder_text"]), str(r["target_username"])))
                        conn.execute("UPDATE reminders SET status = 'completed' WHERE id = ?", (r["id"],))

                for rem_id, thread_id, user_id, username, rem_text, target_user in due:
                    tag = f"@{target_user}" if target_user else f"@{username}"
                    creator_note = f" (set by @{username})" if target_user and target_user.lower() != username.lower() else ""
                    msg = f"⏰ **REMINDER ALERT** for {tag}!{creator_note}\n🔔 {rem_text}"
                    if self.dispatch_callback:
                        try:
                            self.dispatch_callback(thread_id, msg)
                        except Exception:
                            pass
            except Exception:
                pass
            time.sleep(3)

