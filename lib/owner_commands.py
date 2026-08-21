"""Owner-only operational and administrative commands controlled from Instagram DMs."""
from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import config
import settings
from lib.database import Database

OWNER_COMMANDS = {
    "admin", "botstatus", "health", "stats", "cleartmp", "restart", "reload",
    "sudo", "update", "clearsession", "homealert", "reports", "pendingreports",
    "resolve", "dbstats", "vacuum", "dbcompact", "uptime", "broadcast",
    "gban", "gunban", "banned", "banlist", "autofollow", "autofollowback",
}


@dataclass(frozen=True)
class OwnerResult:
    handled: bool = False
    response: str | None = None
    restart: bool = False
    home_alert: bool = False
    broadcast_text: str | None = None


class OwnerCommands:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.started_at = time.monotonic()

    @staticmethod
    def is_owner(username: str | None = None, user_id: str | None = None) -> bool:
        normalized = str(username or "").lower().strip().lstrip("@")
        if config.is_owner(normalized, user_id):
            return True
        bot_username = config.USERNAME.lower().strip().lstrip("@")
        return bool(config.ALLOW_SELF_COMMANDS and bot_username and normalized == bot_username)

    def handle(self, text: str, username: str, user_id: str | None = None) -> OwnerResult:
        stripped = text.strip()
        matched_prefix = None
        for p in (getattr(settings, "PREFIX", "."), ".", ",", "!", "/"):
            if stripped.startswith(p):
                matched_prefix = p
                break
        if not matched_prefix:
            return OwnerResult()
        prefix = matched_prefix
        command_line = stripped[len(matched_prefix):].strip()
        if not command_line:
            return OwnerResult()
        parts = command_line.split(maxsplit=1)
        command = parts[0].lower().rstrip(",:;.")
        if command not in OWNER_COMMANDS:
            return OwnerResult()
        if not self.is_owner(username, user_id):
            return OwnerResult(True, "⛔ Owner-only command. Access denied.")

        if command in {"admin", "sudo"}:
            return OwnerResult(
                True,
                f"👑 **{settings.BOT_NAME.upper()} OWNER CONTROL CENTER**\n"
                f"• Operations: {prefix}botstatus • {prefix}health • {prefix}uptime • {prefix}stats\n"
                f"• Maintenance: {prefix}cleartmp • {prefix}dbstats • {prefix}vacuum • {prefix}restart\n"
                f"• Moderation Reports: {prefix}reports • {prefix}resolve <id>\n"
                f"• Broadcast: {prefix}broadcast <text>\n"
                f"• Group Controls: {prefix}kick • {prefix}remove • {prefix}add • {prefix}warn • {prefix}ban • {prefix}antilink"
            )

        if command in {"uptime"}:
            uptime = int(time.monotonic() - self.started_at)
            hours, rem = divmod(uptime, 3600)
            minutes, seconds = divmod(rem, 60)
            return OwnerResult(True, f"⏱️ Bot Uptime: {hours} hours, {minutes} minutes, {seconds} seconds.")

        if command in {"botstatus", "health"}:
            uptime = int(time.monotonic() - self.started_at)
            hours, rem = divmod(uptime, 3600)
            minutes, seconds = divmod(rem, 60)
            stats = self.database.stats()
            ram_str = "N/A"
            try:
                with open("/proc/meminfo", "r", encoding="utf-8") as f:
                    mem_data = f.read()
                total_kb = int(re.search(r"MemTotal:\s+(\d+)", mem_data).group(1))
                avail_kb = int(re.search(r"MemAvailable:\s+(\d+)", mem_data).group(1))
                ram_str = f"{avail_kb/1024/1024:.1f}GB free / {total_kb/1024/1024:.1f}GB total"
            except Exception:
                pass
            return OwnerResult(
                True,
                f"✅ **{settings.BOT_NAME} System Health**\n"
                f"• Uptime: {hours}h {minutes}m {seconds}s\n"
                f"• Host RAM: {ram_str}\n"
                f"• Database: {stats['users']} users • {stats['messages']} msgs • {stats['bans']} bans\n"
                f"• Rate Limits: {config.MAX_REPLIES_PER_HOUR}/chat/h • {config.MAX_GLOBAL_REPLIES_PER_HOUR}/acc/h\n"
                f"• Supervision: Realtime MQTT + Chromium Headless Active",
            )

        if command == "stats":
            stats = self.database.stats()
            top_active = self.database.top_users(limit=5)
            top_lines = "\n".join(f"  {i+1}. @{u} ({c} msgs)" for i, (u, c) in enumerate(top_active)) or "  None"
            return OwnerResult(
                True,
                f"📊 **BOT ACTIVITY STATISTICS**:\n"
                f"• Registered Users: {stats['users']:,}\n"
                f"• Processed Messages: {stats['messages']:,}\n"
                f"• Moderation Bans: {stats['bans']:,}\n\n"
                f"🏆 **Top Active Users**:\n{top_lines}"
            )

        if command in {"dbstats", "dbcompact", "vacuum"}:
            db_path = settings.BASE_DIR / "data" / "bot.db"
            size_kb = db_path.stat().st_size / 1024 if db_path.exists() else 0
            if command in {"dbcompact", "vacuum"}:
                with self.database.lock, self.database._connect() as conn:
                    conn.execute("VACUUM")
                new_size_kb = db_path.stat().st_size / 1024 if db_path.exists() else 0
                return OwnerResult(True, f"🧹 Vacuum completed. Database size: {size_kb:.1f}KB → {new_size_kb:.1f}KB.")
            return OwnerResult(True, f"💾 SQLite Database: {size_kb:.1f}KB ({db_path.name})")

        if command == "cleartmp":
            removed = 0
            temp = settings.BASE_DIR / "temp"
            if temp.exists():
                for child in temp.iterdir():
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
                    removed += 1
            return OwnerResult(True, f"🧹 Cleared {removed} temporary media item(s).")

        if command == "homealert":
            return OwnerResult(handled=True, home_alert=True)

        if command in {"reports", "pendingreports"}:
            reports = self.database.get_pending_reports(limit=15)
            if not reports:
                return OwnerResult(True, "📋 No pending violation reports.")
            lines = ["📋 **PENDING GC VIOLATION REPORTS**:"]
            for rep in reports:
                lines.append(
                    f"#{rep['id']} | @{rep['offender_username']} | {rep['rule_broken']}\n"
                    f"   Reason: {rep['reason']}\n"
                    f"   Msg: \"{rep['snippet']}\"\n"
                    f"   Time: {rep['created_at']}"
                )
            lines.append(f"\nUse `{prefix}resolve <id>` to mark a report resolved.")
            return OwnerResult(True, "\n\n".join(lines))

        if command == "resolve":
            args = parts[1].split() if len(parts) > 1 else []
            if not args or not args[0].isdigit():
                return OwnerResult(True, f"Usage: {prefix}resolve <report_id>")
            rep_id = int(args[0])
            resolved = self.database.resolve_report(rep_id)
            if resolved:
                return OwnerResult(True, f"✅ Report #{rep_id} marked as resolved.")
            return OwnerResult(True, f"⚠️ Report #{rep_id} not found or already resolved.")

        if command == "broadcast":
            args = parts[1].strip() if len(parts) > 1 else ""
            if not args:
                return OwnerResult(True, f"Usage: {prefix}broadcast <announcement message>")
            return OwnerResult(handled=True, response=f"📢 Broadcast queued for group chats.", broadcast_text=args)

        if command in {"banned", "banlist"}:
            banned = self.database.ban_list()
            if not banned:
                return OwnerResult(True, "📋 No users are currently banned in the database.")
            lines = [f"🚫 **DATABASE BANNED USERS** ({len(banned)}):"]
            for b in banned:
                uname = b.get("username") or b.get("user_id")
                role = "👑 Owner Ban" if b.get("banned_by") == "owner" else "🛡️ Admin Ban"
                reason = b.get("reason") or "No reason provided"
                scope = f" (Thread {b.get('thread_id')})" if b.get("thread_id") != "global" else " (Global)"
                lines.append(f"• @{uname}{scope}\n   By: {role} | Reason: {reason}")
            return OwnerResult(True, "\n".join(lines))

        if command == "gban":
            args = parts[1].split(maxsplit=1) if len(parts) > 1 else []
            if not args:
                return OwnerResult(True, f"Usage: {prefix}gban <username|user_id> [reason]")
            raw_target = args[0].lstrip("@").strip()
            reason = args[1].strip() if len(args) > 1 else "Global ban by bot owner"
            self.database.ban_user("global", raw_target, reason, banned_by="owner")
            return OwnerResult(True, f"🌐 @{raw_target} is now globally banned from all bot interactions.")

        if command == "gunban":
            args = parts[1].split() if len(parts) > 1 else []
            if not args:
                return OwnerResult(True, f"Usage: {prefix}gunban <username|user_id>")
            raw_target = args[0].lstrip("@").strip()
            self.database.unban_user("global", raw_target)
            return OwnerResult(True, f"🌐 @{raw_target} has been globally unbanned.")

        if command in {"autofollow", "autofollowback"}:
            args = parts[1].split() if len(parts) > 1 else []
            current = self.database.bot_setting("auto_follow_back") or "on"
            if not args or args[0].lower() in {"status", "info"}:
                count = self.database.get_followed_users_count()
                return OwnerResult(True, f"👥 **Auto Follow Back**: {current.upper()}\n📊 Total auto-followed users: {count}\nUsage: {prefix}autofollow on|off")
            action = args[0].lower()
            if action in {"on", "off", "enable", "disable"}:
                state = "on" if action in {"on", "enable"} else "off"
                self.database.set_bot_setting("auto_follow_back", state)
                return OwnerResult(True, f"👥 Auto Follow Back is now **{state.upper()}**.")
            return OwnerResult(True, f"Usage: {prefix}autofollow on|off|status")

        if command in {"restart", "reload"}:
            return OwnerResult(handled=True, response="🔄 Restarting bot daemon...", restart=True)

        return OwnerResult()
