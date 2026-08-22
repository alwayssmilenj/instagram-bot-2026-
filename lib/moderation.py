"""Instagram group-chat moderation with owner/admin authorization."""
from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone

import config
import settings
from lib.database import Database
from lib.gc_monitor import normalize_leetspeak

URL_RE = re.compile(
    r"(?:https?://[^\s]+|www\.[^\s]+|(?:[a-zA-Z0-9-]+\.)+(?:com|org|net|io|me|gg|co|link|app|xyz|dev|info|site|online|top|club|vip|live|tv|cc|ru|in|uk|us|ca|de|fr|jp|cn|ly|gl|is|gd|to)(?:/[^\s]*)?|(?:t\.me|wa\.me|discord\.gg|instagram\.com|vm\.tiktok\.com|youtu\.be|bit\.ly|tinyurl\.com|linktr\.ee)(?:/[^\s]*)?)",
    re.I
)
BAD_WORDS = {
    "fuck", "fucker", "fuckers", "fucking", "fucks", "fck", "fuk",
    "bitch", "bitches", "bitching", "btch",
    "asshole", "assholes", "dumbass", "dipshit", "bastard",
    "motherfucker", "motherfuckers", "motherfucking", "mfucker",
    "cunt", "slut", "whore", "dickhead", "dick", "pussy",
    "nigger", "nigga", "faggot", "retard", "chutiya", "madarchod", "bhenchod", "gandu", "pendejo", "puta",
}
ROOT_BAD_WORDS = (
    "motherfucker", "asshole", "fuck", "bitch", "bastard", "cunt", "slut", "whore",
    "nigger", "nigga", "faggot", "retard", "chutiya", "madarchod", "bhenchod", "gandu", "pendejo", "puta",
)


def check_badword(text: str) -> bool:
    """Detect profanity, slurs, and toxic terms across leetspeak, emoji insertion, and homoglyphs."""
    if not text:
        return False
    lowered = text.lower()
    tokens = re.findall(r"[a-z]+", lowered)
    if any(w in BAD_WORDS or any(root in w for root in ROOT_BAD_WORDS) for w in tokens):
        return True
    try:
        norm_text, norm_despaced, norm_no_punct, norm_collapsed, emoji_stripped = normalize_leetspeak(text)
        for variant in (norm_text, norm_despaced, norm_no_punct, norm_collapsed, emoji_stripped.lower()):
            if not variant:
                continue
            v_tokens = re.findall(r"[a-z]+", variant)
            if any(w in BAD_WORDS or any(root in w for root in ROOT_BAD_WORDS) for w in v_tokens):
                return True
            if any(root in variant for root in ROOT_BAD_WORDS):
                return True
    except Exception:
        pass
    return False

GROUP_COMMANDS = {
    "groupinfo", "gc", "group", "infogroup", "staff", "tagall", "everyone", "all", "mentionall",
    "add", "setname", "title", "rename", "mute", "unmute",
    "antilink", "antibadword", "warn", "warnings", "warns", "warncount", "warnlist",
    "clearwarn", "unwarn", "delwarn", "rmwarn", "ban", "unban", "banned", "banlist", "gban", "gunban",
    "kick", "remove", "rm", "promote", "demote", "resetlink", "setting", "settings",
    "antispam", "antlink", "antlinks", "badword", "antibadwords", "spam",
    "members", "admins", "adminlist", "whoami", "botadmin", "rules", "setrules",
    "reports", "pendingreports",
    "antiraid", "raidthreshold", "raid_threshold", "rthreshold", "lockdown",
    "audit", "auditlog", "activity",
}
ADMIN_COMMANDS = {
    "add", "setname", "title", "rename", "mute", "unmute", "antilink", "antibadword", "warn",
    "ban", "unban", "banned", "banlist", "gban", "gunban", "kick", "remove", "rm", "clearwarn", "unwarn", "delwarn", "rmwarn",
    "promote", "demote", "resetlink", "setting", "antispam", "antlink", "antlinks",
    "badword", "antibadwords", "spam", "setrules", "reports", "pendingreports", "tagall",
    "everyone", "all", "mentionall",
    "antiraid", "raidthreshold", "raid_threshold", "rthreshold", "lockdown",
    "audit", "auditlog", "activity",
}


@dataclass(frozen=True)
class ModerationResult:
    handled: bool = False
    response: str | None = None
    blocked: bool = False


class AntiRaidSystem:
    """Tracks burst joins, mass mentions, and triggers automated emergency lockdown."""

    def __init__(self, join_window: float = 15.0, default_threshold: int = 5, mass_mention_threshold: int = 5) -> None:
        self.join_window = join_window
        self.default_threshold = default_threshold
        self.mass_mention_threshold = mass_mention_threshold
        self.join_history: dict[str, deque[float]] = defaultdict(deque)
        self.known_members: dict[str, set[str]] = defaultdict(set)
        self.lock = threading.Lock()

    def record_join(self, thread_id: str, user_id: str, username: str = "", threshold: int | None = None) -> bool:
        """Record a member join event and return True if join burst threshold in sliding window is exceeded."""
        tid = str(thread_id)
        limit = threshold if threshold is not None and threshold >= 2 else self.default_threshold
        now = time.monotonic()
        with self.lock:
            history = self.join_history[tid]
            while history and history[0] < now - self.join_window:
                history.popleft()
            history.append(now)
            if len(history) >= limit:
                return True
        return False

    def check_mass_mentions(self, text: str, threshold: int | None = None) -> tuple[bool, int, list[str]]:
        """Detect if message contains excessive @user mentions."""
        limit = threshold if threshold is not None and threshold >= 2 else self.mass_mention_threshold
        mentions = re.findall(r"@[a-zA-Z0-9._]+", text)
        unique_mentions = list(dict.fromkeys(m.lower() for m in mentions))
        count = len(unique_mentions)
        return (count >= limit, count, unique_mentions)

    def trigger_auto_lockdown(
        self,
        database: Database,
        thread_id: str,
        reason: str,
        actor_id: str = "system",
        actor_username: str = "AntiRaid",
    ) -> str:
        """Activate emergency lockdown on thread and log security audit event."""
        tid = str(thread_id)
        database.set_lockdown(tid, True)
        database.log_audit_event(
            thread_id=tid,
            actor_id=actor_id,
            actor_username=actor_username,
            action="auto_lockdown",
            reason=reason,
        )
        return (
            f"🚨 **EMERGENCY AUTO-LOCKDOWN ACTIVATED!** 🚨\n"
            f"• Reason: {reason}\n"
            f"• Action: Non-admin messages are blocked to protect group safety.\n"
            f"• Resolution: Admins can deactivate anytime using `.lockdown off`."
        )

    def reset_thread(self, thread_id: str) -> None:
        """Clear join history for a thread."""
        tid = str(thread_id)
        with self.lock:
            self.join_history.pop(tid, None)


class GroupModerator:
    def __init__(self, client, database: Database, browser_remover: object | None = None) -> None:
        self.client = client
        self.database = database
        self.browser_remover = browser_remover
        self.anti_raid = AntiRaidSystem()
        self.spam_events: dict[tuple[str, str], deque[tuple[float, str]]] = defaultdict(deque)
        self.spam_seen_order: deque[str] = deque(maxlen=5000)
        self.spam_seen: set[str] = set()

    @staticmethod
    def _thread_id(thread: object) -> str:
        if isinstance(thread, dict):
            return str(thread.get("id") or thread.get("thread_id") or thread.get("pk") or "")
        return str(getattr(thread, "id", getattr(thread, "thread_id", getattr(thread, "pk", ""))))

    @staticmethod
    def _is_group(thread: object) -> bool:
        if isinstance(thread, dict):
            return bool(thread.get("is_group", False))
        return bool(getattr(thread, "is_group", False))

    @staticmethod
    def _admin_user_ids(thread: object) -> set[str]:
        if isinstance(thread, dict):
            raw = thread.get("admin_user_ids") or thread.get("admin_ids") or []
        else:
            raw = getattr(thread, "admin_user_ids", None) or getattr(thread, "admin_ids", None) or []
        return {str(item) for item in raw if item is not None and str(item) != ""}

    @staticmethod
    def _thread_title(thread: object) -> str:
        if isinstance(thread, dict):
            return str(thread.get("thread_title") or thread.get("title") or "Unnamed group")
        return str(getattr(thread, "thread_title", getattr(thread, "title", "Unnamed group")) or "Unnamed group")

    @staticmethod
    def _users(thread: object) -> list[object]:
        if isinstance(thread, dict):
            return list(thread.get("users") or [])
        return list(getattr(thread, "users", None) or [])

    @staticmethod
    def _user_id(user: object) -> str:
        if isinstance(user, dict):
            return str(user.get("pk") or user.get("id") or "")
        return str(getattr(user, "pk", getattr(user, "id", "")))

    @staticmethod
    def _username(user: object) -> str:
        if isinstance(user, dict):
            uname = user.get("username")
            if uname:
                return str(uname).lstrip("@")
            return str(user.get("full_name") or user.get("pk") or "unknown").lstrip("@")
        uname = getattr(user, "username", None)
        if uname:
            return str(uname).lstrip("@")
        return str(getattr(user, "pk", getattr(user, "id", "unknown"))).lstrip("@")

    def is_admin(self, thread: object, sender_id: str, username: str) -> bool:
        if config.is_owner(username, sender_id):
            return True
        return str(sender_id) in self._admin_user_ids(thread)

    def should_review_content(self, text: str, thread: object, sender_id: str, username: str) -> bool:
        if not self._is_group(thread) or self.is_admin(thread, sender_id, username):
            return False
        settings = self.database.thread_settings(self._thread_id(thread))
        has_badword = check_badword(text)
        return bool(
            (settings["antilink"] and URL_RE.search(text))
            or (settings["antibadword"] and has_badword)
        )

    def _target(self, arguments: list[str], thread: object) -> tuple[str, str] | None:
        if not arguments:
            return None
        raw = arguments[0].lstrip("@").rstrip(",;:")
        username = raw.lower()
        if not username:
            return None
        for user in self._users(thread):
            if self._username(user).lower() == username or self._user_id(user) == raw:
                return self._user_id(user), self._username(user)
        if username.isdigit():
            return username, f"user_{username}"
        try:
            return str(self.client.user_id_from_username(username)), username
        except Exception:
            return None

    def _member_target(self, arguments: list[str], thread: object) -> tuple[str, str] | None:
        if not arguments:
            return None
        raw = arguments[0].lstrip("@").rstrip(",;:")
        username = raw.lower()
        if not username:
            return None
        for user in self._users(thread):
            if self._username(user).lower() == username or self._user_id(user) == raw:
                return self._user_id(user), self._username(user)
        return None

    def is_spam(self, message_id: str, text: str, thread: object, sender_id: str, username: str, timestamp: object = None) -> bool:
        if not self._is_group(thread) or self.is_admin(thread, sender_id, username):
            return False
        thread_id = self._thread_id(thread)
        if not self.database.thread_settings(thread_id)["antispam"] or str(message_id) in self.spam_seen:
            return False
        if timestamp is not None:
            if isinstance(timestamp, datetime):
                current = datetime.now(timestamp.tzinfo or timezone.utc)
                stamped = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
                if abs((current - stamped).total_seconds()) > 30:
                    return False
            elif isinstance(timestamp, (int, float)) or (isinstance(timestamp, str) and timestamp.replace(".", "", 1).isdigit()):
                try:
                    ts_float = float(timestamp)
                    if ts_float > 1e14:
                        ts_float /= 1e6
                    elif ts_float > 1e11:
                        ts_float /= 1e3
                    if ts_float > 0 and abs(time.time() - ts_float) > 30:
                        return False
                except (ValueError, OverflowError):
                    pass
        msg_str = str(message_id)
        if len(self.spam_seen_order) >= 5000:
            self.spam_seen.discard(self.spam_seen_order.popleft())
        self.spam_seen_order.append(msg_str)
        self.spam_seen.add(msg_str)

        key = (thread_id, str(sender_id))
        now = time.monotonic()
        normalized = " ".join(text.lower().split())[:300]
        events = self.spam_events[key]
        while events and events[0][0] < now - 10:
            events.popleft()
        events.append((now, normalized))
        if len(self.spam_events) > 2000:
            stale = [k for k, q in self.spam_events.items() if not q or q[-1][0] < now - 60]
            for k in stale:
                self.spam_events.pop(k, None)
        duplicates = Counter(value for _, value in events if value)
        return len(events) >= 6 or (bool(normalized) and duplicates[normalized] >= 3)

    def bot_is_admin(self, thread: object) -> bool:
        bot_id = str(getattr(self.client, "user_id", "") or "")
        bot_name = config.USERNAME.lower().lstrip("@")
        if not bot_id and bot_name:
            for member in self._users(thread):
                if self._username(member).lower() == bot_name:
                    bot_id = self._user_id(member)
                    break
        if not bot_id:
            return False
        return bot_id in self._admin_user_ids(thread)

    def _remove_user(self, thread: object, user_id: str) -> tuple[bool, str]:
        if not self.bot_is_admin(thread):
            return False, "Bot needs Instagram group admin permission to remove them."
        user_id = str(user_id)
        bot_id = str(getattr(self.client, "user_id", "") or "")
        bot_name = config.USERNAME.lower().lstrip("@")
        if bot_id and user_id == bot_id:
            return False, "The bot cannot remove itself."
        target_username = None
        for member in self._users(thread):
            if self._user_id(member) != user_id:
                continue
            target_username = self._username(member)
            if config.is_owner(target_username, user_id):
                return False, "The configured owner cannot be removed by the bot."
            if bot_name and target_username.lower() == bot_name:
                return False, "The bot cannot remove itself."
            break
        if not target_username:
            if user_id.isdigit() and hasattr(self.client, "username_from_user_id"):
                try:
                    target_username = str(self.client.username_from_user_id(int(user_id)))
                except Exception:
                    pass
            if not target_username:
                return False, "Target is not a current member of this group."
        
        tid = self._thread_id(thread)
        if self.browser_remover is not None:
            try:
                thread_num = int(tid) if str(tid).isdigit() else tid
                ok, status = self.browser_remover.remove(thread_num, target_username)
                if ok:
                    return ok, status
            except Exception:
                pass

        if hasattr(self.client, "private_request"):
            for endpoint in [
                f"direct_v2/threads/{tid}/remove_user/",
                f"direct_v2/threads/{tid}/remove_participant/",
                f"direct_v2/threads/{tid}/participants/remove/",
            ]:
                try:
                    res = self.client.private_request(
                        endpoint,
                        data={"user_id": str(user_id), "user_ids": json.dumps([str(user_id)])},
                    )
                    if isinstance(res, dict) and res.get("status") == "ok":
                        return True, f"Removed @{target_username} from the group."
                except Exception:
                    pass

        if hasattr(self.client, "direct_thread_remove_user"):
            try:
                ok = self.client.direct_thread_remove_user(tid, int(user_id) if str(user_id).isdigit() else user_id)
                if ok:
                    return True, f"Removed @{target_username} from the group."
            except Exception:
                pass

        if hasattr(self.client, "direct_thread_remove_users"):
            try:
                ok = self.client.direct_thread_remove_users(tid, [int(user_id) if str(user_id).isdigit() else user_id])
                if ok:
                    return True, f"Removed @{target_username} from the group."
            except Exception:
                pass

        if self.browser_remover is None:
            return False, "Chrome group removal is not configured."
        return False, "Chrome removal failed safely before completing the action."

    def inspect_content(self, text: str, thread: object, sender_id: str, username: str, spam: bool = False) -> ModerationResult:
        if not self._is_group(thread):
            return ModerationResult()
        thread_id = self._thread_id(thread)
        settings = self.database.thread_settings(thread_id)
        is_adm = self.is_admin(thread, sender_id, username)

        # 1. Enforce lockdown mode
        if settings.get("lockdown") and not is_adm:
            return ModerationResult(
                response=f"🔒 @{username}, this group is currently in LOCKDOWN. Non-admin messages are restricted.",
                blocked=True,
            )

        if is_adm:
            return ModerationResult()

        # 2. Check mass mention raid
        if settings.get("antiraid"):
            is_mass, count_mentions, _ = self.anti_raid.check_mass_mentions(
                text, threshold=int(settings.get("raid_threshold", 5))
            )
            if is_mass:
                lockdown_alert = self.anti_raid.trigger_auto_lockdown(
                    self.database,
                    thread_id,
                    f"Mass mention raid by @{username} ({count_mentions} mentions)",
                )
                self.database.ban_user(thread_id, sender_id, f"Mass mention raid ({count_mentions} mentions)")
                removed, r_status = self._remove_user(thread, sender_id)
                self.database.log_audit_event(
                    thread_id,
                    "system",
                    "AntiRaid",
                    "ban",
                    target_id=sender_id,
                    target_username=username,
                    reason=f"Mass mention raid auto-ban ({count_mentions} mentions)",
                )
                return ModerationResult(
                    response=f"🚨 @{username} triggered mass mention raid protection! {r_status}\n{lockdown_alert}",
                    blocked=True,
                )

        violation = None
        if settings["antilink"] and URL_RE.search(text):
            violation = "links are disabled"
        has_badword = check_badword(text)
        if settings["antibadword"] and has_badword:
            violation = "prohibited language"
        if spam and settings["antispam"]:
            violation = "spam or repeated messages"
        if not violation:
            return ModerationResult()
        count = self.database.add_warning(thread_id, sender_id)
        maximum = int(settings["max_warnings"])
        if count >= maximum:
            ban_reason = f"Reached {maximum} warnings ({violation})"
            self.database.ban_user(thread_id, sender_id, ban_reason)
            self.database.log_audit_event(
                thread_id,
                "system",
                "Moderator",
                "ban",
                target_id=sender_id,
                target_username=username,
                reason=ban_reason,
            )
            removed, removal_status = self._remove_user(thread, sender_id)
            return ModerationResult(
                response=f"🚫 @{username} reached {maximum} warnings. {removal_status}",
                blocked=True,
            )
        self.database.log_audit_event(
            thread_id,
            "system",
            "Moderator",
            "warn",
            target_id=sender_id,
            target_username=username,
            reason=f"Warning {count}/{maximum} ({violation})",
        )
        return ModerationResult(response=f"⚠️ @{username}: {violation}. Warning {count}/{maximum}.", blocked=True)

    def handle(self, text: str, thread: object, sender_id: str, username: str) -> ModerationResult:
        stripped = text.strip()
        matched_prefix = None
        for p in (getattr(settings, "PREFIX", "."), ".", ",", "!", "/"):
            if stripped.startswith(p):
                matched_prefix = p
                break
        if not matched_prefix:
            return ModerationResult()
        command_line = stripped[len(matched_prefix):].strip()
        parts = command_line.split()
        if not parts:
            return ModerationResult()
        command = parts[0].lower().rstrip(",:;.!?")
        alias_map = {
            "antlink": "antilink", "antlinks": "antilink",
            "badword": "antibadword", "antibadwords": "antibadword",
            "spam": "antispam",
            "unwarn": "clearwarn", "delwarn": "clearwarn", "rmwarn": "clearwarn",
            "warns": "warnlist", "warncount": "warnlist",
            "everyone": "tagall", "all": "tagall", "mentionall": "tagall",
            "adminlist": "admins",
            "title": "setname", "rename": "setname",
            "group": "groupinfo", "infogroup": "groupinfo",
            "auditlog": "audit", "activity": "audit",
            "raid_threshold": "raidthreshold", "rthreshold": "raidthreshold",
        }
        command = alias_map.get(command, command)
        arguments = parts[1:]
        if command not in GROUP_COMMANDS:
            return ModerationResult()
        if not self._is_group(thread):
            return ModerationResult(True, "This command only works in an Instagram group chat.")

        thread_id = self._thread_id(thread)
        is_bot_owner = config.is_owner(username, sender_id)
        if self.database is not None and hasattr(self.database, "is_banned") and self.database.is_banned(thread_id, sender_id, username) and not is_bot_owner:
            return ModerationResult(
                True,
                f"🚫 @{username}, you are banned from using bot commands. Contact the bot owner (@jinshi_1) to get unbanned.",
            )

        admin = self.is_admin(thread, sender_id, username)
        if command in ADMIN_COMMANDS and not admin:
            return ModerationResult(True, "⛔ Only the group owner/admin can use this command.")

        users = self._users(thread)
        if command == "settings":
            current = self.database.thread_settings(thread_id)
            return ModerationResult(True, "⚙️ GROUP SETTINGS\n" + "\n".join([
                f"antilink: {'on' if current['antilink'] else 'off'}",
                f"antibadword: {'on' if current['antibadword'] else 'off'}",
                f"antispam: {'on' if current['antispam'] else 'off'}",
                f"antiraid: {'on' if current.get('antiraid') else 'off'}",
                f"raid_threshold: {current.get('raid_threshold', 5)}",
                f"lockdown: {'on' if current.get('lockdown') else 'off'}",
                f"bot_muted: {'on' if current['bot_muted'] else 'off'}",
                f"admin_only: {'on' if current['admin_only'] else 'off'}",
                f"max_warnings: {current['max_warnings']}",
            ]) + "\nThreshold action: ban + verified headless Chrome removal\nAdmin usage: .setting <name> <value>")
        if command == "setting":
            if len(arguments) < 2:
                return ModerationResult(True, "Usage: .setting antilink|antibadword|antispam|antiraid|lockdown|mute|adminonly|maxwarnings|raidthreshold <on|off|value>")
            key, value = arguments[0].lower().rstrip(",:;"), arguments[1].lower().rstrip(",:;")
            flag_map = {
                "antilink": "antilink", "antlink": "antilink",
                "antibadword": "antibadword", "badword": "antibadword",
                "antispam": "antispam", "spam": "antispam",
                "antiraid": "antiraid", "raid": "antiraid",
                "lockdown": "lockdown",
                "mute": "bot_muted", "adminonly": "admin_only",
            }
            if key in flag_map and value in {"on", "off"}:
                enabled = value == "on"
                self.database.set_thread_flag(thread_id, flag_map[key], enabled)
                self.database.log_audit_event(thread_id, sender_id, username, f"{flag_map[key]}_{'enable' if enabled else 'disable'}", reason=f"Setting {key} set to {value}")
                return ModerationResult(True, f"✅ {key} set to {value}")
            if key in {"maxwarnings", "max_warnings"} and value.isdigit() and 1 <= int(value) <= 10:
                self.database.set_max_warnings(thread_id, int(value))
                self.database.log_audit_event(thread_id, sender_id, username, "set_max_warnings", reason=f"max_warnings set to {value}")
                return ModerationResult(True, f"✅ maxwarnings set to {value}")
            if key in {"raidthreshold", "raid_threshold"} and value.isdigit() and 2 <= int(value) <= 50:
                self.database.set_raid_threshold(thread_id, int(value))
                self.database.log_audit_event(thread_id, sender_id, username, "set_raid_threshold", reason=f"raid_threshold set to {value}")
                return ModerationResult(True, f"✅ raidthreshold set to {value}")
            return ModerationResult(True, "Invalid setting. Use on/off, maxwarnings 1-10, or raidthreshold 2-50.")
        if command == "antiraid":
            if not arguments:
                current_state = bool(self.database.thread_settings(thread_id).get("antiraid"))
                return ModerationResult(True, f"🛡️ **ANTIRAID** is currently **{'ENABLED (ON)' if current_state else 'DISABLED (OFF)'}** for this chat.\nUse `.antiraid on` or `.antiraid off` to change.")
            arg_clean = arguments[0].lower().rstrip(",:;")
            if arg_clean not in {"on", "off", "1", "0", "true", "false", "enable", "disable"}:
                return ModerationResult(True, "Usage: .antiraid on|off")
            enabled = arg_clean in {"on", "1", "true", "enable"}
            self.database.set_antiraid(thread_id, enabled)
            self.database.log_audit_event(
                thread_id,
                sender_id,
                username,
                "antiraid_enable" if enabled else "antiraid_disable",
                reason=f"Anti-Raid turned {'on' if enabled else 'off'} by @{username}",
            )
            return ModerationResult(True, f"🛡️ Anti-Raid protection {'enabled' if enabled else 'disabled'}.")
        if command == "raidthreshold":
            if not arguments:
                curr_threshold = int(self.database.thread_settings(thread_id).get("raid_threshold", 5))
                return ModerationResult(True, f"🛡️ Raid join threshold is currently **{curr_threshold}** joins / 15s.\nUse `.raidthreshold <count>` (2-50) to change.")
            if not arguments[0].isdigit():
                return ModerationResult(True, "Usage: .raidthreshold <count (2-50)>")
            count = int(arguments[0])
            if not (2 <= count <= 50):
                return ModerationResult(True, "⚠️ Threshold must be between 2 and 50.")
            self.database.set_raid_threshold(thread_id, count)
            self.database.log_audit_event(
                thread_id,
                sender_id,
                username,
                "set_raid_threshold",
                reason=f"Raid burst threshold set to {count} by @{username}",
            )
            return ModerationResult(True, f"🛡️ Raid join threshold set to {count} joins / 15s.")
        if command == "lockdown":
            if not arguments:
                current_state = bool(self.database.thread_settings(thread_id).get("lockdown"))
                return ModerationResult(True, f"🔒 **LOCKDOWN** is currently **{'ACTIVE (ON)' if current_state else 'INACTIVE (OFF)'}** for this chat.\nUse `.lockdown on` or `.lockdown off` to change.")
            arg_clean = arguments[0].lower().rstrip(",:;")
            if arg_clean not in {"on", "off", "1", "0", "true", "false", "enable", "disable"}:
                return ModerationResult(True, "Usage: .lockdown on|off")
            enabled = arg_clean in {"on", "1", "true", "enable"}
            self.database.set_lockdown(thread_id, enabled)
            self.database.log_audit_event(
                thread_id,
                sender_id,
                username,
                "lockdown_on" if enabled else "lockdown_off",
                reason=f"Lockdown {'activated' if enabled else 'deactivated'} by @{username}",
            )
            return ModerationResult(
                True,
                f"🔒 Thread lockdown {'ACTIVATED. Non-admin messages are restricted.' if enabled else 'DEACTIVATED. Group chat restored to normal.'}",
            )
        if command in {"audit", "auditlog", "activity"}:
            limit = 10
            if arguments and arguments[0].isdigit():
                limit = min(25, max(1, int(arguments[0])))
            logs = self.database.get_recent_audit_logs(thread_id, limit=limit)
            if not logs:
                return ModerationResult(True, "📋 No moderation activity recorded for this chat yet.")
            emoji_map = {
                "ban": "🔨",
                "unban": "🔓",
                "gban": "🌐",
                "gunban": "🌐",
                "kick": "👢",
                "remove": "👢",
                "warn": "⚠️",
                "clearwarn": "🧹",
                "mute": "🔇",
                "unmute": "🔊",
                "lockdown_on": "🔒",
                "lockdown_off": "🔓",
                "auto_lockdown": "🚨",
                "antiraid_enable": "🛡️",
                "antiraid_disable": "🛡️",
                "set_raid_threshold": "🛡️",
                "setting_change": "⚙️",
                "rules_update": "📜",
            }
            lines = [f"📋 RECENT ACTIVITY AUDIT LOG (Thread: {thread_id}):"]
            for entry in logs:
                act = str(entry.get("action", ""))
                icon = emoji_map.get(act, "📋")
                actor = entry.get("actor_username") or entry.get("actor_id") or "system"
                target = entry.get("target_username") or entry.get("target_id")
                reason = entry.get("reason") or ""
                created = str(entry.get("created_at") or "")[:19]

                target_str = f" → @{str(target).lstrip('@')}" if target else ""
                reason_str = f" | {reason}" if reason else ""
                lines.append(f"• {icon} [{act.upper()}] @{str(actor).lstrip('@')}{target_str}{reason_str} ({created})")
            return ModerationResult(True, "\n".join(lines))
        if command in {"groupinfo", "gc"}:
            title = self._thread_title(thread)
            return ModerationResult(True, f"👥 {title}\nMembers: {len(users)}\nAdmins: {len(self._admin_user_ids(thread))}\nThread: {thread_id}")
        if command == "members":
            names = [f"@{self._username(user)}" for user in users]
            return ModerationResult(True, f"👥 Members ({len(names)}):\n" + (" ".join(names)[:1600] if names else "No member list available"))
        if command == "whoami":
            is_owner = config.is_owner(username, sender_id)
            role = "owner (Bot Owner)" if is_owner else "admin" if admin else "member"
            return ModerationResult(True, f"👤 @{username}\nUser: {sender_id}\nRole: {role}\nThread: {thread_id}")
        if command == "botadmin":
            return ModerationResult(True, "✅ Bot has group-admin add/removal permission." if self.bot_is_admin(thread) else "⚠️ Bot is not a group admin; add admin permission before using .add/.kick/.remove.")
        if command == "rules":
            rules = str(self.database.thread_settings(thread_id)["rules"])
            return ModerationResult(True, f"📜 GROUP RULES\n{rules}" if rules else "No group rules set. Admin: .setrules <rules>")
        if command == "setrules":
            rules = " ".join(arguments).strip()
            if not rules:
                return ModerationResult(True, "Usage: .setrules <group rules>")
            self.database.set_thread_rules(thread_id, rules)
            self.database.log_audit_event(thread_id, sender_id, username, "rules_update", reason=f"Rules updated: {rules[:60]}")
            return ModerationResult(True, "✅ Group rules updated. Use .rules to view them.")
        if command in {"staff", "admins"}:
            admin_ids = self._admin_user_ids(thread)
            names = [f"@{self._username(user)}" for user in users if self._user_id(user) in admin_ids]
            return ModerationResult(True, "👑 Group admins:\n" + ("\n".join(names) if names else "No admin list available"))
        if command == "tagall":
            valid_users = [
                self._username(user)
                for user in users
                if self._username(user) not in {"unknown", "None", ""}
            ]
            seen: set[str] = set()
            unique_names: list[str] = []
            for u in valid_users:
                if u.lower() not in seen:
                    seen.add(u.lower())
                    unique_names.append(f"@{u}")

            if not unique_names:
                return ModerationResult(True, "📢 No group members found to tag.")

            custom_msg = " ".join(arguments).strip()
            header = f"📢 ATTENTION EVERYONE 📢\n💬 {custom_msg}\n\n" if custom_msg else "📢 Group Members Announcement:\n\n"
            tag_str = ""
            for tag in unique_names:
                if len(header) + len(tag_str) + len(tag) + 1 > 1800:
                    break
                tag_str += (" " if tag_str else "") + tag
            return ModerationResult(True, header + tag_str)
        if command == "setname":
            title = " ".join(arguments).strip()[:100]
            if not title:
                return ModerationResult(True, "Usage: .setname <new group name>")
            try:
                tid = int(thread_id) if thread_id.isdigit() else thread_id
                self.client.direct_thread_update_title(tid, title)
                return ModerationResult(True, f"✅ Group renamed to {title}")
            except Exception as error:
                return ModerationResult(True, f"⚠️ Could not rename group: {error}")
        if command == "add":
            if not arguments:
                return ModerationResult(True, "Usage: .add @username")
            target_name = arguments[0].lstrip("@").rstrip(",;:").lower()
            if not target_name:
                return ModerationResult(True, "Usage: .add @username")
            if any(self._username(member).lower() == target_name for member in users):
                return ModerationResult(True, f"@{target_name} is already in this group.")

            tid = int(thread_id) if str(thread_id).isdigit() else thread_id
            target_id = None
            if hasattr(self.client, "user_id_from_username"):
                try:
                    target_id = self.client.user_id_from_username(target_name)
                except Exception:
                    pass
            if target_id is None and hasattr(self.client, "user_info_by_username"):
                try:
                    uinfo = self.client.user_info_by_username(target_name)
                    target_id = getattr(uinfo, "pk", getattr(uinfo, "id", None))
                except Exception:
                    pass

            # 1. Direct REST API Add
            if target_id is not None:
                try:
                    uid = int(target_id) if str(target_id).isdigit() else target_id
                    if hasattr(self.client, "direct_thread_add_users"):
                        ok = self.client.direct_thread_add_users(tid, [uid])
                        if ok:
                            return ModerationResult(True, f"✅ Added @{target_name} to the group.")
                except Exception:
                    pass

                if hasattr(self.client, "private_request"):
                    for endpoint in [
                        f"direct_v2/threads/{tid}/add_user/",
                        f"direct_v2/threads/{tid}/participants/add/",
                    ]:
                        try:
                            res = self.client.private_request(
                                endpoint,
                                data={"user_ids": json.dumps([str(target_id)])},
                            )
                            if isinstance(res, dict) and res.get("status") == "ok":
                                return ModerationResult(True, f"✅ Added @{target_name} to the group.")
                        except Exception:
                            pass

            # 2. Chrome Playwright Fallback
            if self.browser_remover is not None:
                try:
                    added, status = self.browser_remover.add(tid, target_name)
                    return ModerationResult(True, f"{'✅' if added else '⚠️'} {status}")
                except Exception as error:
                    return ModerationResult(True, f"⚠️ Chrome add failed: {error}")

            if target_id is None:
                return ModerationResult(True, f"⚠️ Could not resolve @{target_name}. Please verify the username.")
            return ModerationResult(True, f"⚠️ Could not add @{target_name}. Ensure the bot has group admin permissions.")
        if command in {"mute", "unmute"}:
            enabled = command == "mute"
            self.database.set_thread_flag(thread_id, "bot_muted", enabled)
            self.database.log_audit_event(thread_id, sender_id, username, "mute" if enabled else "unmute", reason=f"Bot commands {'muted' if enabled else 'unmuted'}")
            return ModerationResult(True, "🔇 Bot muted; only admins are accepted." if enabled else "🔊 Bot commands unmuted.")
        if command in {"antilink", "antibadword", "antispam"}:
            if not arguments:
                current_state = bool(self.database.thread_settings(thread_id).get(command))
                return ModerationResult(True, f"🛡️ **{command.upper()}** is currently **{'ENABLED (ON)' if current_state else 'DISABLED (OFF)'}** for this chat.\nUse `.{command} on` or `.{command} off` to change.")
            arg_clean = arguments[0].lower().rstrip(",:;")
            if arg_clean not in {"on", "off", "1", "0", "true", "false", "enable", "disable"}:
                return ModerationResult(True, f"Usage: .{command} on|off")
            enabled = arg_clean in {"on", "1", "true", "enable"}
            self.database.set_thread_flag(thread_id, command, enabled)
            self.database.log_audit_event(thread_id, sender_id, username, f"{command}_{'enable' if enabled else 'disable'}", reason=f"{command} set to {'on' if enabled else 'off'}")
            return ModerationResult(True, f"✅ {command} {'enabled' if enabled else 'disabled'}")
        if command == "warnlist":
            warned = self.database.warning_list(thread_id)
            lines = [f"@{name}: {count}" for _, name, count in warned]
            return ModerationResult(True, "⚠️ WARNINGS\n" + ("\n".join(lines) if lines else "No active warnings."))
        if command in {"reports", "pendingreports"}:
            reports = self.database.get_pending_reports(thread_id, limit=10)
            if not reports:
                return ModerationResult(True, "📋 No pending violation reports for this group chat.")
            lines = ["📋 PENDING GC VIOLATION REPORTS:"]
            for rep in reports:
                lines.append(
                    f"#{rep['id']} | @{rep['offender_username']} | {rep['rule_broken']}\n"
                    f"   Reason: {rep['reason']}\n"
                    f"   Msg: \"{rep['snippet']}\"\n"
                    f"   Time: {rep['created_at']}"
                )
            return ModerationResult(True, "\n\n".join(lines))
        if command == "clearwarn":
            target = self._target(arguments, thread)
            if not target:
                return ModerationResult(True, "Usage: .clearwarn @username")
            cleared = self.database.clear_warnings(thread_id, target[0])
            self.database.log_audit_event(thread_id, sender_id, username, "clearwarn", target_id=target[0], target_username=target[1], reason=f"Cleared {cleared} warning(s)")
            return ModerationResult(True, f"✅ Cleared {cleared} warning(s) for @{target[1]}.")
        if command in {"banned", "banlist"}:
            banned = self.database.ban_list(thread_id)
            if not banned:
                return ModerationResult(True, "📋 No users are currently banned in this group chat.")
            lines = [f"🚫 BANNED USERS ({len(banned)}):"]
            for b in banned:
                uname = b.get("username") or b.get("user_id")
                role = "👑 Bot Owner" if b.get("banned_by") == "owner" else "🛡️ GC Admin"
                reason = b.get("reason") or "No reason provided"
                scope = " (Global Ban)" if b.get("thread_id") == "global" else ""
                lines.append(f"• @{uname}{scope}\n   By: {role} | Reason: {reason}")
            return ModerationResult(True, "\n".join(lines))
        if command in {"gban", "gunban"}:
            if not config.is_owner(username, sender_id):
                return ModerationResult(True, "⛔ Global ban/unban commands are restricted to the bot owner (@jinshi_1).")
            target = self._target(arguments, thread)
            if not target:
                return ModerationResult(True, f"Usage: .{command} @username")
            target_id, target_name = target
            if command == "gban":
                if config.is_owner(target_name, target_id):
                    return ModerationResult(True, "👑 The configured owner cannot be banned.")
                reason = " ".join(arguments[1:]).strip() or "Global ban by bot owner"
                self.database.ban_user("global", target_id, reason, banned_by="owner")
                if target_name:
                    self.database.ban_user("global", target_name.lower().lstrip("@"), reason, banned_by="owner")
                self.database.log_audit_event("global", sender_id, username, "gban", target_id=target_id, target_username=target_name, reason=reason)
                return ModerationResult(True, f"🌐 @{target_name} is now globally banned from all bot interactions.")
            self.database.unban_user("global", target_id)
            if target_name:
                self.database.unban_user("global", target_name.lower().lstrip("@"))
            self.database.log_audit_event("global", sender_id, username, "gunban", target_id=target_id, target_username=target_name, reason="Global unban by bot owner")
            return ModerationResult(True, f"🌐 @{target_name} has been globally unbanned.")
        if command in {"warn", "warnings", "ban", "unban"}:
            target = self._target(arguments, thread)
            if not target:
                return ModerationResult(True, f"Usage: .{command} @username")
            target_id, target_name = target
            bot_id = str(getattr(self.client, "user_id", "") or "")
            bot_name = config.USERNAME.lower().lstrip("@")
            is_caller_owner = config.is_owner(username, sender_id)
            is_target_owner = config.is_owner(target_name, target_id)
            is_target_admin = str(target_id) in self._admin_user_ids(thread)

            if command in {"warn", "ban"}:
                if is_target_owner:
                    return ModerationResult(True, "👑 The configured owner is protected from moderation actions.")
                if (bot_id and target_id == bot_id) or (bot_name and target_name.lower() == bot_name):
                    return ModerationResult(True, "🤖 The bot account is protected from moderation actions.")
                if is_target_admin and not is_caller_owner:
                    action_verb = "ban" if command == "ban" else "warn"
                    return ModerationResult(
                        True,
                        f"⛔ Group admins cannot {action_verb} other group admins (@{target_name}). Only the bot owner (@jinshi_1) can moderate admins.",
                    )
            if command == "warn":
                count = self.database.add_warning(thread_id, target_id)
                maximum = int(self.database.thread_settings(thread_id)["max_warnings"])
                if count >= maximum:
                    self.database.ban_user(thread_id, target_id, f"Reached {maximum} warnings")
                    if target_name:
                        self.database.ban_user(thread_id, target_name.lower().lstrip("@"), f"Reached {maximum} warnings")
                    self.database.log_audit_event(thread_id, sender_id, username, "ban", target_id=target_id, target_username=target_name, reason=f"Reached {maximum} warnings")
                    removed, removal_status = self._remove_user(thread, target_id)
                    return ModerationResult(True, f"🚫 @{target_name}: warning {count}/{maximum}. {removal_status}")
                self.database.log_audit_event(thread_id, sender_id, username, "warn", target_id=target_id, target_username=target_name, reason=f"Warning {count}/{maximum}")
                return ModerationResult(True, f"⚠️ @{target_name}: warning {count}/{maximum}")
            if command == "warnings":
                return ModerationResult(True, f"@{target_name} has {self.database.warning_count(thread_id, target_id)} warning(s).")
            if command == "ban":
                banned_by_role = "owner" if is_caller_owner else "admin"
                reason = " ".join(arguments[1:]).strip() or ("Banned by bot owner" if is_caller_owner else "Banned by group admin")
                self.database.ban_user(thread_id, target_id, reason, banned_by=banned_by_role)
                if target_name:
                    self.database.ban_user(thread_id, target_name.lower().lstrip("@"), reason, banned_by=banned_by_role)
                self.database.log_audit_event(thread_id, sender_id, username, "ban", target_id=target_id, target_username=target_name, reason=reason)
                return ModerationResult(
                    True,
                    f"🚫 @{target_name} is now banned. They cannot use any bot commands in this group.",
                )
            # Unban logic with owner protection
            ban_info = self.database.get_ban_info(thread_id, target_id, target_name)
            if ban_info and ban_info.get("banned_by") == "owner" and not is_caller_owner:
                return ModerationResult(
                    True,
                    f"⛔ @{target_name} was banned by the bot owner (@jinshi_1). Group admins cannot override or unban them.",
                )
            self.database.unban_user(thread_id, target_id)
            if target_name:
                self.database.unban_user(thread_id, target_name.lower().lstrip("@"))
            self.database.log_audit_event(thread_id, sender_id, username, "unban", target_id=target_id, target_username=target_name, reason="Unbanned by admin/owner")
            return ModerationResult(True, f"✅ @{target_name} unbanned. They can now use bot commands again.")
        if command in {"kick", "remove", "rm"}:
            target = self._member_target(arguments, thread) or self._target(arguments, thread)
            if not target:
                return ModerationResult(True, f"Usage: .{command} @current_group_member")
            target_id, target_name = target
            bot_id = str(getattr(self.client, "user_id", "") or "")
            bot_name = config.USERNAME.lower().lstrip("@")
            is_caller_owner = config.is_owner(username, sender_id)
            is_target_owner = config.is_owner(target_name, target_id)
            is_target_admin = str(target_id) in self._admin_user_ids(thread)

            if is_target_owner:
                return ModerationResult(True, "👑 The configured owner cannot be removed by the bot.")
            if (bot_id and target_id == bot_id) or (bot_name and target_name.lower() == bot_name):
                return ModerationResult(True, "🤖 The bot cannot remove itself.")
            if is_target_admin and not is_caller_owner:
                return ModerationResult(
                    True,
                    f"⛔ Group admins cannot remove other group admins (@{target_name}). Only the bot owner (@jinshi_1) can remove admins.",
                )
            removed, status = self._remove_user(thread, target_id)
            self.database.log_audit_event(thread_id, sender_id, username, "kick", target_id=target_id, target_username=target_name, reason=status)
            return ModerationResult(True, f"{'✅' if removed else '⚠️'} @{target_name}: {status}")
        if command in {"promote", "demote", "resetlink"}:
            return ModerationResult(True, f"Instagram does not expose a safe {command} API. No account action was attempted.")
        return ModerationResult()
