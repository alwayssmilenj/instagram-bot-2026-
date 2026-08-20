"""Safe, high-utility, reusable commands adapted from KnightBot's general command set."""
from __future__ import annotations

import os
import random
import re
import time
from dataclasses import dataclass

import config
import settings
from commands.extended import ExtendedCommands
from commands.menu import MenuBuilder


@dataclass(frozen=True)
class MessageContext:
    username: str
    user_id: str
    thread_id: str


@dataclass(frozen=True)
class PiesRequest:
    country: str


@dataclass(frozen=True)
class StickerRequest:
    mood: str


@dataclass(frozen=True)
class SongRequest:
    query: str


@dataclass(frozen=True)
class LyricsRequest:
    query: str


@dataclass(frozen=True)
class VideoRequest:
    query: str


@dataclass(frozen=True)
class AIRequest:
    prompt: str


@dataclass(frozen=True)
class TTSRequest:
    text: str
    lang: str = "en"


@dataclass(frozen=True)
class SearchRequest:
    query: str


@dataclass(frozen=True)
class WikiRequest:
    topic: str


@dataclass(frozen=True)
class CanvasRequest:
    kind: str
    text1: str
    text2: str = ""


@dataclass(frozen=True)
class TeachRequest:
    fact: str


@dataclass(frozen=True)
class GitHubRequest:
    kind: str
    target: str = ""


CommandResponse = (
    str
    | SongRequest
    | PiesRequest
    | StickerRequest
    | LyricsRequest
    | VideoRequest
    | AIRequest
    | TTSRequest
    | SearchRequest
    | WikiRequest
    | CanvasRequest
    | TeachRequest
    | GitHubRequest
    | None
)


def clean_media_query(query: str) -> str:
    """Normalize and deduplicate repeated media queries (e.g. '.song starboy starboy' or 'faded, faded')."""
    if not query:
        return ""
    q = query.strip()
    if q.startswith(("http://", "https://")):
        return q

    if (q.startswith('"') and q.endswith('"')) or (q.startswith("'") and q.endswith("'")):
        q = q[1:-1].strip()

    # 1. Strip redundant leading command prefixes
    while True:
        lowered = q.lower()
        matched = False
        for prefix in (
            ".song ", "song ", "!song ", "/song ",
            ".play ", "play ", "!play ", "/play ",
            ".video ", "video ", "!video ", "/video ",
            ".lyrics ", "lyrics ", "!lyrics ", "/lyrics ",
            ".lyric ", "lyric ", "!lyric ", "/lyric ",
        ):
            if lowered.startswith(prefix):
                q = q[len(prefix):].strip()
                matched = True
                break
        if not matched:
            break

    # 2. Check for separator-based duplicates: e.g. "faded, faded", "faded - faded", "faded | faded", "faded / faded"
    for sep in (",", "-", "|", "/", ";"):
        parts = [p.strip() for p in q.split(sep) if p.strip()]
        if len(parts) == 2 and parts[0].lower() == parts[1].lower():
            q = parts[0]
            break

    # 3. Check for repeated phrase halves (word-level): e.g. "starboy starboy", "shape of you shape of you"
    words = q.split()
    if len(words) >= 2 and len(words) % 2 == 0:
        half = len(words) // 2
        first_half = [w.lower().strip(" \t\"'.,;!?") for w in words[:half]]
        second_half = [w.lower().strip(" \t\"'.,;!?") for w in words[half:]]
        if first_half == second_half:
            q = " ".join(words[:half])

    # 4. Check for repeated string pattern (case-insensitive) if length >= 4:
    mid = len(q) // 2
    for offset in range(-2, 3):
        split_pt = mid + offset
        if 1 <= split_pt < len(q):
            left = q[:split_pt].strip().rstrip(",-;|/ \t\"'")
            right = q[split_pt:].strip().lstrip(",-;|/ \t\"'")
            if left and right and left.lower() == right.lower():
                q = left
                break

    return q.strip()


class CommandRouter:
    def __init__(self, started_at: float | None = None) -> None:
        self.started_at = started_at or time.monotonic()
        self.extended = ExtendedCommands()
        self.menu = MenuBuilder()

    def route(self, text: str, context: MessageContext) -> CommandResponse:
        text = (text or "").strip()
        if not text.startswith(settings.PREFIX):
            return None
        command_line = text[len(settings.PREFIX):].strip()
        if not command_line:
            return None
        command, *arguments = command_line.split()
        command = command.lower().rstrip(",:;.!?")

        if command in {"help", "menu", "commands"}:
            return self.menu.render(arguments[0] if arguments else None)

        if command in {"ping", "p"}:
            return f"🏓 Pong! {settings.BOT_NAME} (Ineffa) is online and operational ⚡"

        if command in {"alive", "status", "botstatus", "system"}:
            uptime = int(time.monotonic() - self.started_at)
            hours, rem = divmod(uptime, 3600)
            minutes, seconds = divmod(rem, 60)
            uptime_str = f"{hours}h {minutes}m {seconds}s" if hours else f"{minutes}m {seconds}s"
            ram_info = ""
            try:
                with open("/proc/meminfo", "r", encoding="utf-8") as f:
                    mem_data = f.read()
                total_kb = int(re.search(r"MemTotal:\s+(\d+)", mem_data).group(1))
                avail_kb = int(re.search(r"MemAvailable:\s+(\d+)", mem_data).group(1))
                ram_info = f"\n• Host RAM: {avail_kb/1024/1024:.1f}GB free / {total_kb/1024/1024:.1f}GB total"
            except Exception:
                pass
            return (
                f"🤖 **{settings.BOT_NAME} (Ineffa) System Status**\n"
                f"• Uptime: {uptime_str}\n"
                f"• State: Active & Realtime Responsive ⚡{ram_info}\n"
                f"• Prefix: `{settings.PREFIX}` • Mode: Full Sovereign"
            )

        if command == "whoami":
            is_owner = config.is_owner(context.username, context.user_id)
            role = "👑 Bot Owner (FULL_SOVEREIGN)" if is_owner else "👤 Chat Member (STANDARD_USER)"
            return (
                f"👤 **USER IDENTIFIER PROFILE**:\n"
                f"• Username: @{context.username.lstrip('@')}\n"
                f"• User ID: {context.user_id}\n"
                f"• Thread ID: {context.thread_id}\n"
                f"• Role: {role}"
            )

        if command == "owner":
            primary = config.OWNER_USERNAME
            all_owners = sorted(list({o.lstrip("@").lower() for o in (config.OWNER_USERNAMES | {primary}) if o}))
            owner_list = ", ".join(f"@{o}" for o in all_owners)
            return f"👑 **Bot Owner**: @{primary}\n🛡️ **Authorized Owners**: {owner_list}"

        if command == "id":
            return f"👤 User ID: {context.user_id}\n💬 Thread ID: {context.thread_id}"

        if command == "echo":
            return " ".join(arguments)[:1000] if arguments else f"Usage: {settings.PREFIX}echo hello"

        if command in {"ai", "chatbot", "ask", "chat"}:
            prompt = " ".join(arguments).strip()
            return AIRequest(prompt) if prompt else f"Usage: {settings.PREFIX}ai <question or message>"

        if command in {"teach", "remember", "learn"}:
            return TeachRequest(" ".join(arguments)) if arguments else f"Usage: {settings.PREFIX}{command} <fact to remember>"

        if command in {"projects", "featuredprojects"}:
            return GitHubRequest(kind="projects", target=arguments[0] if arguments else "")

        if command in {"github", "repo"}:
            return GitHubRequest(kind="repo", target=arguments[0] if arguments else "")

        if command in {"tts", "voice", "say", "speak"}:
            if not arguments:
                return f"Usage: {settings.PREFIX}{command} <text> or {settings.PREFIX}{command} <lang_code> <text>"
            known_langs = {
                "en", "es", "fr", "de", "hi", "ja", "ko", "zh", "ru", "pt", "it",
                "ar", "tr", "id", "ms", "vi", "th", "uk", "nl", "pl", "sv",
                "hinglish", "english", "spanish", "hindi", "japanese", "korean", "french", "german",
            }
            if len(arguments) > 1 and arguments[0].lower() in known_langs:
                lang = arguments[0].lower()
                tts_text = " ".join(arguments[1:])
            else:
                lang = "auto"
                tts_text = " ".join(arguments)
            return TTSRequest(text=tts_text, lang=lang)

        if command in {"search", "google", "ddg"}:
            return SearchRequest(" ".join(arguments)) if arguments else f"Usage: {settings.PREFIX}{command} <query>"

        if command in {"wiki", "wikipedia"}:
            return WikiRequest(" ".join(arguments)) if arguments else f"Usage: {settings.PREFIX}{command} <topic>"

        if command in {"meme", "memecard"}:
            full = " ".join(arguments).strip()
            if not full:
                return f"Usage: {settings.PREFIX}meme top text | bottom text"
            if "|" in full:
                parts = [p.strip() for p in full.split("|", 1)]
                text1 = parts[0]
                text2 = parts[1] if len(parts) > 1 else ""
                if not text1 and not text2:
                    return f"Usage: {settings.PREFIX}meme top text | bottom text"
                return CanvasRequest(kind="meme", text1=text1, text2=text2)
            return CanvasRequest(kind="meme", text1=full, text2="")

        if command in {"quotecard", "card"}:
            return CanvasRequest(kind="quote", text1=" ".join(arguments)) if arguments else f"Usage: {settings.PREFIX}card <quote text>"

        if command in {"sticker", "asticker"}:
            mood = arguments[0].lower() if arguments else "random"
            moods = {"random", "happy", "angry", "smug", "sleepy", "love", "shocked", "sad", "chaos"}
            return StickerRequest(mood) if len(arguments) <= 1 and mood in moods else f"Usage: {settings.PREFIX}{command} [happy|angry|smug|sleepy|love|shocked|sad|chaos]"

        if command in {"song", "play"}:
            cleaned = clean_media_query(" ".join(arguments))
            return SongRequest(cleaned) if cleaned else f"Usage: {settings.PREFIX}{command} <song name or YouTube link>"

        if command in {"lyrics", "lyric", "lyrcs"}:
            cleaned = clean_media_query(" ".join(arguments))
            return LyricsRequest(cleaned) if cleaned else f"Usage: {settings.PREFIX}{command} <song name and artist>"

        if command == "video":
            cleaned = clean_media_query(" ".join(arguments))
            return VideoRequest(cleaned) if cleaned else f"Usage: {settings.PREFIX}video <video name or YouTube link>"

        pies_countries = {"india", "malaysia", "thailand", "china", "indonesia", "japan", "korea", "vietnam"}
        if command in {"pies", "pied"}:
            return PiesRequest(arguments[0].lower()) if arguments and arguments[0].lower() in pies_countries else f"Usage: {settings.PREFIX}{command} <{'|'.join(sorted(pies_countries))}>"
        if command in pies_countries:
            return PiesRequest(command)

        if command in {"8ball", "eightball"}:
            return random.choice([
                "Yes, definitely ✨", "Without a doubt 🔮", "Most likely 💫",
                "Ask again later ⏳", "Better not tell you now 🤫", "Very doubtful 💀",
                "My sources say no 🛑", "Signs point to yes 🎯"
            ])

        if command == "quote":
            return random.choice([
                "Small steps every single day lead to colossal results over time 🌱✨",
                "Make it work, make it right, make it fast ⚡",
                "Consistency beats intensity when intensity is temporary 💎",
                "The only limit to our realization of tomorrow is our doubts of today 🚀",
                "Fortune favors the brave and the prepared 🛡️",
            ])

        if command == "fact":
            return random.choice([
                "Honey can remain edible for thousands of years without spoiling 🍯",
                "Octopuses have three hearts and blue copper-based blood 🐙",
                "A day on Venus is longer than a whole year on Venus 🪐",
                "Bananas are naturally radioactive due to high potassium-40 isotopes 🍌",
                "There are more possible iterations of a game of chess than atoms in the observable universe ♟️",
            ])

        if command == "joke":
            return random.choice([
                "Why do programmers prefer dark mode? Because light attracts bugs 🐛",
                "There are only 10 types of people in the world: those who understand binary, and those who don't 💻",
                "Why did the JavaScript developer wear glasses? Because they didn't C# 👓",
                "A SQL query walks into a bar, walks up to two tables, and asks: 'Can I join you?' 🍺",
            ])

        negative_commands = {"insult", "simp", "stupid", "wasted"}
        if command in negative_commands:
            target_raw = arguments[0] if arguments else context.username
            clean_target = target_raw.rstrip("/").rsplit("/", 1)[-1].strip("@,;:!?()[]\"'").lower()
            targets_owner = (
                (not arguments and config.is_owner(context.username, context.user_id))
                or config.is_owner(clean_target)
            )
            if targets_owner:
                return "👑 Owners are protected from negative commands."

        extended = self.extended.handle(command, arguments, context.username)
        if extended is not None:
            return extended

        return f"Unknown command. Send {settings.PREFIX}help for the complete command menu."

    @staticmethod
    def help_text() -> str:
        return MenuBuilder().render()
