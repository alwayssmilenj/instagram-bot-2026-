"""Full categorization and dynamic reference of all available KnightBot (Ineffa) commands."""
from __future__ import annotations

import re

import config
import settings

CATEGORY_PAGES: dict[str, tuple[str, list[str]]] = {
    "core": (
        "⭐ CORE COMMANDS",
        [
            ".ping — check bot latency and responsiveness",
            ".alive / .status — bot system health, host RAM, and uptime",
            ".whoami — show your user ID and permission profile",
            ".id — show your user ID and thread ID",
            ".owner — display bot owner and authorized co-owners",
            ".help [command|category] — command reference",
            ".menu [category|all] — categorized command menu",
            ".ai <prompt> — chat with Ineffa AI",
            ".teach <fact> — teach Ineffa a memory fact",
            ".autoreply <on|off|status> — toggle AI replies in GC",
            ".autoreplyvn <on|off|status> — toggle voice replies",
            ".echo <text> — repeat text cleanly",
        ],
    ),
    "media": (
        "🎬 MEDIA & DOWNLOADS",
        [
            ".video <name|link> — download video (H.264 MP4)",
            ".song <name|link> — download music (voice note)",
            ".play <name|link> — alias for .song",
            ".lyrics <song name> — search synced song lyrics",
            ".meme <top | bottom> — create meme card image",
            ".card <text> — create gradient quote card",
            ".sticker [mood] — create animated mood sticker",
            ".tts [lang] <text> — speak text as voice note",
            ".pies <country> — photos from 8 Asian countries",
            ".featuredprojects — explore curated open-source tools",
        ],
    ),
    "games": (
        "🎮 GAMES & CASINO",
        [
            ".rank / .level — display your XP stats, title & level progress",
            ".leaderboard / .top — group chat activity and XP leaderboard",
            ".rps <rock|paper|scissors> — rock paper scissors match",
            ".slots — spin 3-reel slot machine with jackpot multipliers",
            ".roll [NdS|sides] — roll RPG dice (e.g. .roll 2d20+5, .roll 100)",
            ".coinflip / .coin — flip a heads/tails coin",
            ".dice — roll a 6-sided die",
            ".choose a | b | c — pick between choices",
            ".random [min] [max] — random integer generator",
            ".truth — 50+ curated truth questions",
            ".dare — 50+ fun dare challenges",
            ".8ball <question> — mystic 8-ball fortune",
        ],
    ),
    "fun": (
        "🎭 FUN & SOCIAL",
        [
            ".botgf [@user|off] — toggle dedicated girlfriend mode",
            ".aura [@user] — scan aura points and tier rating",
            ".iq [@user] — big-brain calculated IQ scanner",
            ".vibe [@user] — check current aura & vibe",
            ".compliment [@user] — send uplifting compliment",
            ".flirt [@user] — send charming flirt line",
            ".insult [@user] — playful roast (sovereign protected)",
            ".roast — roast yourself",
            ".ship @user1 [@user2] — matchmaker score & chemistry",
            ".simp [@user] — check simp percentage",
            ".stupid [@user] — check stupid percentage",
            ".wasted [@user] — check wasted percentage",
            ".character [@user] — analyze character archetype & aura",
            ".quote — inspirational quote",
            ".fact — fascinating trivia fact",
            ".joke — programming or dad joke",
            ".shayari — friendship & poetic shayari",
            ".roseday [@user] — send a virtual rose",
            ".anime — recommend an anime masterpiece",
        ],
    ),
    "tools": (
        "🛠️ TEXT & UTILITIES (70+ TOOLS)",
        [
            ".calc <expr> — evaluate math with trig, sqrt, log, pi, e",
            ".reverse <text> — reverse string",
            ".upper <text> / .lower <text> — case transform",
            ".title <text> — Title Case",
            ".length <text> — count characters",
            ".words <text> — count words",
            ".repeat <count> <text> — repeat up to 5x",
            ".mock <text> — mOcKiNg tExT",
            ".clap <text> — clap 👏 between 👏 words",
            ".morse <text> / .unmorse <code> — Morse code converter",
            ".base64 <text> / .unbase64 <code> — Base64 encoder/decoder",
            ".binary <text> / .unbinary <code> — binary 01s",
            ".hex <text> / .unhex <code> — hex encoder/decoder",
            ".rot13 <text> / .caesar <shift> <text> — ciphers",
            ".password [length] — generate secure password",
            ".hash [algo] <text> — MD5, SHA1, SHA256, SHA512",
            ".uuid — generate UUIDv4",
            ".time [zone] — current time across global timezones",
            ".timestamp — Unix epoch timestamp",
            ".date / .day — current date and day",
            ".temperature <val> <C|F|K> <C|F|K> — temperature convert",
            ".bmi <kg> <cm> — calculate body mass index",
            ".age <YYYY-MM-DD> — calculate age in years & days",
            ".countdown <1-50> — count down with rocket",
            ".shuffle <words> — randomize word order",
            ".jsonmin / .jsonpretty <json> — format JSON",
            ".average / .median / .sum / .min / .max <nums> — statistics",
            ".gcd / .lcm <int1> <int2> — greatest common divisor / LCM",
            ".prime <integer> — prime number check",
            ".factorial <integer> — compute factorial",
        ],
    ),
    "info": (
        "🌐 INFORMATION & SEARCH",
        [
            ".search <query> — search the web",
            ".wiki <topic> — Wikipedia article summary",
            ".weather <city> — live weather forecast",
            ".news — latest Google News headlines",
            ".github <username|owner/repo> — GitHub profiles and repos",
            ".spotify <query> — Spotify search link",
            ".translate <lang> <text> — Google Translate (100+ languages)",
            ".urban <slang> — Urban Dictionary search",
        ],
    ),
    "group": (
        "👥 GROUP COMMANDS (In GC)",
        [
            ".groupinfo / .gc — member and admin count",
            ".members — list all group members",
            ".admins / .staff — list group admins",
            ".rules — view group chat rules",
            ".whoami — check your group role",
            ".botadmin — check if bot has admin rights",
            ".tagall [message] — tag all members (Admin only)",
        ],
    ),
    "admin": (
        "🛡️ GROUP ADMIN CONTROLS",
        [
            ".add @user — add member to group",
            ".kick @user / .remove @user / .rm @user — remove member",
            ".warn @user — issue moderation warning",
            ".warnings [@user] — view warning count",
            ".warnlist — view all warned members",
            ".clearwarn @user — clear warnings",
            ".ban @user — block from bot commands",
            ".unban @user — unblock user",
            ".antilink on|off — auto-warn for URLs",
            ".antibadword on|off — auto-warn for slurs/profanity",
            ".antispam on|off — auto-warn for message flooding",
            ".mute / .unmute — mute bot for non-admins",
            ".setname <new name> — rename group",
            ".setrules <rules> — set group rules",
            ".setting <flag> on|off — configure flags",
            ".settings — view all current GC settings",
            ".reports / .pendingreports — view pending GC violation reports",
        ],
    ),
    "owner": (
        "👑 OWNER COMMANDS",
        [
            ".admin / .sudo — owner command overview",
            ".botstatus / .health / .uptime — uptime and health checks",
            ".stats — database and top chatter rankings",
            ".cleartmp — clean temporary media files",
            ".dbstats / .vacuum — database optimization",
            ".reports — view pending violation reports across GCs",
            ".resolve <id> — resolve violation report",
            ".broadcast <text> — dispatch announcement to GCs",
            ".homealert — trigger owner notification",
            ".restart / .reload — restart bot daemon",
        ],
    ),
    "safety": (
        "🔒 SAFETY & RATE LIMITS",
        [
            "All outbound DM replies are strictly rate-limited:",
            f"• Max {config.MAX_REPLIES_PER_HOUR} replies per chat per hour",
            f"• Max {config.MAX_GLOBAL_REPLIES_PER_HOUR} replies globally per hour",
            f"• {config.MIN_REPLY_INTERVAL_SECONDS:g}s cooldown between consecutive user requests",
            "• Owner commands bypass user rate limits safely",
            "• Zero-spam, non-invasive automated moderation",
        ],
    ),
}

CATEGORY_ALIASES: dict[str, str] = {
    "basic": "core",
    "essential": "core",
    "download": "media",
    "downloads": "media",
    "music": "media",
    "video": "media",
    "song": "media",
    "game": "games",
    "casino": "games",
    "social": "fun",
    "roast": "fun",
    "utility": "tools",
    "utilities": "tools",
    "text": "tools",
    "search": "info",
    "web": "info",
    "gc": "group",
    "moderation": "admin",
    "mod": "admin",
    "system": "owner",
    "limits": "safety",
}


class MenuBuilder:
    def __init__(self) -> None:
        self.command_categories: dict[str, str] = {}
        for category, (_, lines) in CATEGORY_PAGES.items():
            for line in lines:
                for command in re.findall(r"\.([a-z0-9-]+)", line.lower()):
                    self.command_categories.setdefault(command, category)

    @staticmethod
    def _footer() -> str:
        return f"\nSend {settings.PREFIX}menu for categories • Owner @{config.OWNER_USERNAME}"

    def home(self) -> str:
        text = (
            f"🤖 {settings.BOT_NAME.upper()} (INEFFA) COMMAND CENTER\n"
            f"Owner: @{config.OWNER_USERNAME}\n\n"
            "⚡ Quick: .ping .alive .video .song .rps .calc .help\n\n"
            "📚 CATEGORIES\n"
            "• core — essentials & status\n"
            "• media — video, music, lyrics, photos, voice\n"
            "• games — interactive chat games & casino\n"
            "• fun — social commands, matchmaking & roasts\n"
            "• tools — 70+ math, text & utility tools\n"
            "• info — weather, news, search, translation\n"
            "• group — group chat information\n"
            "• admin — moderation controls & anti-raid\n"
            "• owner — operations & database maintenance\n"
            "• safety — limits and account protection\n\n"
            "Use .menu games or .help rps\n"
            "Use .menu all for every page"
        )
        return self._with_prefix(text)

    @staticmethod
    def _with_prefix(text: str) -> str:
        return re.sub(r"(?<![\w])\.(?=[a-zA-Z0-9])", lambda _: settings.PREFIX, text)

    def category(self, name: str, include_footer: bool = True) -> str:
        title, lines = CATEGORY_PAGES[name]
        body = "\n".join(f"• {self._with_prefix(line)}" for line in lines)
        if name == "safety":
            body += (
                f"\n• Limits: {config.MAX_REPLIES_PER_HOUR}/chat/hour, "
                f"{config.MAX_GLOBAL_REPLIES_PER_HOUR}/account/hour, "
                f"{config.MIN_REPLY_INTERVAL_SECONDS:g}s user cooldown"
            )
        footer = self._footer() if include_footer else ""
        return f"{title}\n{body}{footer}"

    def render(self, topic: str | None = None) -> str:
        topic_clean = (topic or "home").strip()
        if not topic_clean:
            topic_clean = "home"
        normalized = topic_clean.lower()
        if normalized.startswith(settings.PREFIX.lower()):
            normalized = normalized[len(settings.PREFIX):].strip()
        normalized = normalized.lstrip(".")
        if not normalized:
            normalized = "home"
        if normalized == "home":
            return self.home()
        if normalized == "all":
            pages = "\n\n".join(self.category(name, include_footer=False) for name in CATEGORY_PAGES)
            return f"{self.home()}\n\n{pages}{self._footer()}"
        category = self.command_categories.get(normalized)
        if category:
            return f"Found {settings.PREFIX}{normalized}\n\n{self.category(category)}"
        alias = CATEGORY_ALIASES.get(normalized, normalized)
        if alias in CATEGORY_PAGES:
            return self.category(alias)
        categories = " • ".join(CATEGORY_PAGES)
        return f"Unknown menu topic: {topic}\nCategories: {categories}\nTry {settings.PREFIX}menu media"
