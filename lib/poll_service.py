"""Interactive Group Chat Poll Service for KnightBot."""
from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from typing import Any

from lib.database import Database


class PollService:
    """Manages multi-choice polls with live voting, percentages, and bar charts."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self._init_db()

    def _init_db(self) -> None:
        try:
            with self.database._connect() as conn:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS gc_polls (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        thread_id TEXT NOT NULL,
                        creator_id TEXT NOT NULL,
                        creator_username TEXT NOT NULL,
                        question TEXT NOT NULL,
                        options_json TEXT NOT NULL,
                        votes_json TEXT NOT NULL DEFAULT '{}',
                        status TEXT NOT NULL DEFAULT 'open',
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )"""
                )
        except Exception:
            pass

    def create_poll(self, thread_id: str, creator_id: str, creator_username: str, args_text: str) -> tuple[bool, str]:
        # Close any existing open poll in this thread
        with self.database._connect() as conn:
            conn.execute("UPDATE gc_polls SET status = 'closed' WHERE thread_id = ? AND status = 'open'", (str(thread_id),))

        # Parse quoted arguments e.g. "Question" "Option 1" "Option 2"
        tokens: list[str] = []
        try:
            tokens = shlex.split(args_text)
        except Exception:
            tokens = [t.strip("\"'") for t in args_text.split() if t]

        if len(tokens) < 3:
            return False, "⚠️ Usage: `.poll \"Question\" \"Option 1\" \"Option 2\" ... [up to 5 options]`"

        question = tokens[0].strip()
        options = [opt.strip() for opt in tokens[1:6] if opt.strip()]
        if len(options) < 2:
            return False, "⚠️ A poll requires at least 2 options."

        options_json = json.dumps(options)
        with self.database._connect() as conn:
            cur = conn.execute(
                "INSERT INTO gc_polls (thread_id, creator_id, creator_username, question, options_json) VALUES (?, ?, ?, ?, ?)",
                (str(thread_id), str(creator_id), creator_username.lstrip("@"), question, options_json),
            )
            poll_id = cur.lastrowid

        return True, self._format_poll_display(poll_id, question, options, {}, "open")

    def vote(self, thread_id: str, user_id: str, username: str, option_num_str: str) -> tuple[bool, str]:
        clean_num = option_num_str.strip().lstrip("#")
        if not clean_num.isdigit():
            return False, "⚠️ Usage: `.vote <option_number>` (e.g. `.vote 1`)"

        choice_idx = int(clean_num) - 1

        with self.database._connect() as conn:
            row = conn.execute(
                "SELECT id, question, options_json, votes_json, status FROM gc_polls WHERE thread_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
                (str(thread_id),),
            ).fetchone()

            if not row:
                return False, "⚠️ No active poll in this group chat. Create one with `.poll \"Question\" \"Opt1\" \"Opt2\"`."

            poll_id, question, opt_json, votes_json, status = row
            options = json.loads(opt_json)
            votes: dict[str, int] = json.loads(votes_json) if votes_json else {}

            if choice_idx < 0 or choice_idx >= len(options):
                return False, f"⚠️ Invalid option #{clean_num}. Choose between 1 and {len(options)}."

            votes[str(user_id)] = choice_idx
            conn.execute("UPDATE gc_polls SET votes_json = ? WHERE id = ?", (json.dumps(votes), poll_id))

        chosen_name = options[choice_idx]
        display = self._format_poll_display(poll_id, question, options, votes, "open")
        return True, f"🗳️ @{username.lstrip('@')} voted for **{chosen_name}**!\n\n{display}"

    def poll_status(self, thread_id: str) -> tuple[bool, str]:
        with self.database._connect() as conn:
            row = conn.execute(
                "SELECT id, question, options_json, votes_json, status FROM gc_polls WHERE thread_id = ? ORDER BY id DESC LIMIT 1",
                (str(thread_id),),
            ).fetchone()

        if not row:
            return False, "📋 No polls found for this group chat."

        poll_id, question, opt_json, votes_json, status = row
        options = json.loads(opt_json)
        votes: dict[str, int] = json.loads(votes_json) if votes_json else {}
        return True, self._format_poll_display(poll_id, question, options, votes, status)

    def end_poll(self, thread_id: str, user_id: str, is_admin_or_owner: bool = False) -> tuple[bool, str]:
        with self.database._connect() as conn:
            row = conn.execute(
                "SELECT id, creator_id, question, options_json, votes_json FROM gc_polls WHERE thread_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
                (str(thread_id),),
            ).fetchone()

            if not row:
                return False, "⚠️ No active poll to end."

            poll_id, creator_id, question, opt_json, votes_json = row
            if not is_admin_or_owner and str(creator_id) != str(user_id):
                return False, "⛔ Only the poll creator or group admin can end this poll."

            conn.execute("UPDATE gc_polls SET status = 'closed' WHERE id = ?", (poll_id,))

        options = json.loads(opt_json)
        votes: dict[str, int] = json.loads(votes_json) if votes_json else {}
        display = self._format_poll_display(poll_id, question, options, votes, "closed")
        return True, f"🛑 **POLL CLOSED**\n\n{display}"

    @staticmethod
    def _format_poll_display(poll_id: int, question: str, options: list[str], votes: dict[str, int], status: str) -> str:
        total_votes = len(votes)
        num_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
        counts = [0] * len(options)
        for _, choice in votes.items():
            if 0 <= choice < len(options):
                counts[choice] += 1

        lines = [
            f"📊 **POLL #{poll_id}: {question}**",
            f"Status: {'🟢 Active' if status == 'open' else '🔴 Closed'} | Total Votes: {total_votes}",
            "───────────────────",
        ]

        for i, opt in enumerate(options):
            emoji = num_emojis[i] if i < len(num_emojis) else f"{i+1}."
            count = counts[i]
            pct = (count / total_votes * 100) if total_votes > 0 else 0
            bar_len = int(round(pct / 10))
            bar = "█" * bar_len + "░" * (10 - bar_len)
            lines.append(f"{emoji} **{opt}**\n   {bar} {pct:.0f}% ({count} votes)")

        if status == "open":
            lines.append("───────────────────")
            lines.append("👉 Type `.vote <number>` to cast your vote!")

        return "\n".join(lines)
