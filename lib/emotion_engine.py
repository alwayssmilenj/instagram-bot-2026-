"""Dynamic Emotion & Anger Dynamics Engine for KnightBot (Ineffa).

Provides real-time emotional state tracking, sentiment provocation analysis,
graduated anger escalation, protective fury triggers, and apology de-escalation.
"""

from __future__ import annotations

import enum
import re
import time
from dataclasses import dataclass, field
from typing import Any


class MoodState(str, enum.Enum):
    """Discrete emotional states for the AI persona."""
    CHILL = "chill"
    PLAYFUL = "playful"
    SARCASTIC = "sarcastic"
    ANNOYED = "annoyed"
    ANGRY = "angry"
    PROTECTIVE_RAGE = "protective_rage"
    CHAOTIC_GOOD = "chaotic_good"


class ModerationIntent(str, enum.Enum):
    """Autonomous moderation recommendations triggered by extreme emotional states."""
    NONE = "none"
    ROAST = "roast"
    WARN = "warn"
    MUTE = "mute"
    KICK = "kick"
    BAN = "ban"


@dataclass
class UserEmotionalProfile:
    """Tracks rapport, irritation, and strike levels for a specific user."""
    user_id: str
    username: str
    rapport_score: int = 50  # 0 (mortal enemy) to 100 (beloved bestie)
    strike_count: int = 0
    consecutive_insults: int = 0
    last_interaction_ts: float = field(default_factory=time.time)
    current_directed_mood: MoodState = MoodState.CHILL
    inside_jokes: list[str] = field(default_factory=list)


class EmotionEngine:
    """Manages dynamic persona moods, anger triggers, and emotional transitions."""

    INSULT_PATTERNS = [
        r"\b(?:shut\s+up|fuck\s+you|stfu|bitch|idiot|stupid|dumbass|clown|useless|trash|retard|worthless|kys|ugly|hoe|whore)\b",
        r"\b(?:hate\s+you|you\s+suck|worst\s+bot|kill\s+yourself|die)\b",
        r"\b(?:go\s+away|nobody\s+likes\s+you|cringe\s+bot)\b",
    ]

    APOLOGY_PATTERNS = [
        r"\b(?:sorry|i'm\s+sorry|im\s+sorry|my\s+bad|apologies|forgive\s+me|didn't\s+mean\s+it|i\s+apologize|sry)\b",
        r"\b(?:pls\s+dont\s+ban|please\s+dont\s+kick|chill\s+out|im\s+joking|just\s+a\s+joke|just\s+kidding)\b",
    ]

    PRAISE_PATTERNS = [
        r"\b(?:love\s+you|best\s+bot|you're\s+amazing|ur\s+cool|queen|goat|w\s+bot|good\s+bot|cute|smart|genius)\b",
        r"\b(?:thank\s+you|thanks|ily|ily\s+sm|wholesome|legend)\b",
    ]

    OWNER_DISRESPECT_PATTERNS = [
        r"\b(?:owner\s+sucks|fuck\s+the\s+owner|owner\s+is\s+(?:trash|dumb|stupid|bad|ugly))\b",
        r"\b(?:creator\s+sucks|fuck\s+the\s+creator|developer\s+is\s+(?:trash|dumb|stupid))\b",
    ]

    def __init__(self) -> None:
        self.profiles: dict[str, UserEmotionalProfile] = {}
        self.global_mood: MoodState = MoodState.CHILL
        self._compiled_insults = [re.compile(p, re.IGNORECASE) for p in self.INSULT_PATTERNS]
        self._compiled_apologies = [re.compile(p, re.IGNORECASE) for p in self.APOLOGY_PATTERNS]
        self._compiled_praise = [re.compile(p, re.IGNORECASE) for p in self.PRAISE_PATTERNS]
        self._compiled_owner_disrespect = [re.compile(p, re.IGNORECASE) for p in self.OWNER_DISRESPECT_PATTERNS]

    def get_or_create_profile(self, user_id: str, username: str) -> UserEmotionalProfile:
        if user_id not in self.profiles:
            self.profiles[user_id] = UserEmotionalProfile(user_id=user_id, username=username)
        return self.profiles[user_id]

    def analyze_turn(
        self,
        prompt: str,
        user_id: str,
        username: str,
        is_owner: bool = False,
        is_friend: bool = False,
    ) -> tuple[MoodState, ModerationIntent, str | None]:
        """Analyze message sentiment, update emotional profile, and determine response tone and moderation action."""
        profile = self.get_or_create_profile(user_id, username)
        profile.last_interaction_ts = time.time()
        text = prompt.strip().lower()

        # 1. Check for owner / VIP friend disrespect (Instant Protective Rage)
        if any(p.search(text) for p in self._compiled_owner_disrespect):
            profile.strike_count += 2
            profile.rapport_score = max(0, profile.rapport_score - 30)
            profile.current_directed_mood = MoodState.PROTECTIVE_RAGE
            return (
                MoodState.PROTECTIVE_RAGE,
                ModerationIntent.BAN if profile.strike_count >= 3 else ModerationIntent.KICK,
                "disrespecting my creator/owner is an instant death sentence 💀⚔️",
            )

        # 2. Check for apologies & de-escalation
        if any(p.search(text) for p in self._compiled_apologies):
            if profile.consecutive_insults > 0:
                profile.consecutive_insults = max(0, profile.consecutive_insults - 2)
            profile.strike_count = max(0, profile.strike_count - 1)
            profile.rapport_score = min(100, profile.rapport_score + 10)
            profile.current_directed_mood = MoodState.SARCASTIC if profile.strike_count > 0 else MoodState.PLAYFUL
            return (
                profile.current_directed_mood,
                ModerationIntent.NONE,
                "fine, i'll let it slide this once. don't test my patience again ✨",
            )

        # 3. Check for praise / positive reinforcement
        if any(p.search(text) for p in self._compiled_praise):
            profile.rapport_score = min(100, profile.rapport_score + 5)
            profile.consecutive_insults = 0
            profile.current_directed_mood = MoodState.PLAYFUL if not is_friend else MoodState.CHILL
            return (profile.current_directed_mood, ModerationIntent.NONE, None)

        # 4. Check for direct insults / hostility
        is_insult = any(p.search(text) for p in self._compiled_insults)
        if is_insult and not is_owner:
            profile.consecutive_insults += 1
            profile.strike_count += 1
            profile.rapport_score = max(0, profile.rapport_score - 15)

            # Graduated Anger Escalation
            if profile.consecutive_insults == 1:
                profile.current_directed_mood = MoodState.SARCASTIC
                return (MoodState.SARCASTIC, ModerationIntent.ROAST, None)
            elif profile.consecutive_insults == 2:
                profile.current_directed_mood = MoodState.ANNOYED
                return (MoodState.ANNOYED, ModerationIntent.WARN, "watch your mouth before i send you to the shadow realm 💀")
            elif profile.consecutive_insults == 3:
                profile.current_directed_mood = MoodState.ANGRY
                return (MoodState.ANGRY, ModerationIntent.MUTE, "you've been barking for 3 turns straight. silence time 🔇")
            else:
                profile.current_directed_mood = MoodState.PROTECTIVE_RAGE
                return (MoodState.PROTECTIVE_RAGE, ModerationIntent.KICK, "that's it. pack your bags 🚪👋")

        # 5. Default mood based on rapport
        if profile.rapport_score >= 80 or is_friend or is_owner:
            profile.current_directed_mood = MoodState.PLAYFUL
        elif profile.rapport_score <= 25:
            profile.current_directed_mood = MoodState.SARCASTIC
        else:
            profile.current_directed_mood = MoodState.CHILL

        return (profile.current_directed_mood, ModerationIntent.NONE, None)

    def get_system_prompt_mood_guidance(self, mood: MoodState) -> str:
        """Provide dynamic prompt styling instruction matching current emotional state."""
        guidelines = {
            MoodState.CHILL: "Tone: Relaxed, friendly, Gen-Z casual, concise, supportive.",
            MoodState.PLAYFUL: "Tone: Cheerful, slightly mischievous, high energy, teasing, witty.",
            MoodState.SARCASTIC: "Tone: Dry sarcasm, unbothered, sharp witty comebacks, eye-rolling humor.",
            MoodState.ANNOYED: "Tone: Irritated, blunt, low-tolerance for foolishness, short piercing remarks.",
            MoodState.ANGRY: "Tone: Fierce, commanding, verbal beatdown, zero hesitation to moderate.",
            MoodState.PROTECTIVE_RAGE: "Tone: Unhinged protective elf warrior fury. Defend the realm and roast the offender mercilessly.",
            MoodState.CHAOTIC_GOOD: "Tone: Unpredictable, hilarious, rapid-fire banter, energetic meme references.",
        }
        return guidelines.get(mood, guidelines[MoodState.CHILL])


@dataclass
class VibeEntry:
    user_id: str
    username: str
    thread_id: str
    vibe_text: str
    category: str
    emoji: str
    updated_at: float = field(default_factory=time.time)


class VibeService:
    """Manages user custom vibe statuses, real-time mood broadcasting, and collective group-chat vibe synergy."""

    VIBE_CATEGORIES = {
        "chill": {"emoji": "🌊", "label": "Chill & Relaxed", "keywords": ["chill", "relax", "vibing", "peace", "calm", "cozy"]},
        "hype": {"emoji": "⚡", "label": "Hype & High Energy", "keywords": ["hype", "lit", "energy", "excited", "turn up", "fire", "locked in"]},
        "focus": {"emoji": "🧠", "label": "Deep Focus & Study", "keywords": ["coding", "studying", "focus", "work", "grind", "building", "reading"]},
        "sleepy": {"emoji": "☕", "label": "Sleepy & Dreaming", "keywords": ["sleepy", "tired", "bed", "nap", "exhausted", "coffee"]},
        "chaos": {"emoji": "😈", "label": "Chaotic Goblin", "keywords": ["chaos", "villain", "demon", "feral", "menace", "trolling"]},
        "romantic": {"emoji": "💖", "label": "Soft & Loving", "keywords": ["love", "crush", "romantic", "soft", "sweet", "wholesome"]},
        "gamer": {"emoji": "🎮", "label": "Gaming & In The Zone", "keywords": ["gaming", "game", "valorant", "fortnite", "ranked", "stream", "clutch"]},
        "lofi": {"emoji": "🎧", "label": "Listening to Music", "keywords": ["lofi", "music", "song", "playlist", "spotify", "listening", "headphones"]},
        "mystic": {"emoji": "🔮", "label": "Celestial & Astral", "keywords": ["magic", "stars", "astral", "tarot", "mystic", "witchy"]},
    }

    def __init__(self, database: Any = None) -> None:
        self.database = database
        self.vibes: dict[str, dict[str, VibeEntry]] = {}  # thread_id -> {user_id: VibeEntry}
        self._init_db()

    def _init_db(self) -> None:
        if self.database is not None and hasattr(self.database, "_connect"):
            try:
                with self.database._connect() as conn:
                    conn.execute(
                        """CREATE TABLE IF NOT EXISTS user_vibes (
                            thread_id TEXT NOT NULL,
                            user_id TEXT NOT NULL,
                            username TEXT NOT NULL,
                            vibe_text TEXT NOT NULL,
                            category TEXT NOT NULL DEFAULT 'chill',
                            emoji TEXT NOT NULL DEFAULT '🌊',
                            updated_at REAL NOT NULL,
                            PRIMARY KEY(thread_id, user_id)
                        )"""
                    )
            except Exception:
                pass

    def _detect_category(self, text: str) -> tuple[str, str]:
        lowered = text.lower()
        for cat_key, info in self.VIBE_CATEGORIES.items():
            if any(k in lowered for k in info["keywords"]):
                return cat_key, info["emoji"]
        # Fallback category
        return "chill", "✨"

    def set_vibe(self, thread_id: str, user_id: str, username: str, vibe_text: str) -> tuple[bool, str]:
        """Set a user's custom status/vibe."""
        clean_text = vibe_text.strip()
        if not clean_text:
            return False, "⚠️ Usage: `.vibe set <your current status/mood>` (e.g. `.vibe set Coding late night with lofi 🎧`)"

        clean_text = clean_text[:140]
        cat, emoji = self._detect_category(clean_text)
        t_id = str(thread_id)
        u_id = str(user_id)
        u_name = username.lstrip("@")
        now = time.time()

        entry = VibeEntry(
            user_id=u_id,
            username=u_name,
            thread_id=t_id,
            vibe_text=clean_text,
            category=cat,
            emoji=emoji,
            updated_at=now,
        )

        if t_id not in self.vibes:
            self.vibes[t_id] = {}
        self.vibes[t_id][u_id] = entry

        # Persist to database if available
        if self.database is not None and hasattr(self.database, "_connect"):
            try:
                with self.database._connect() as conn:
                    conn.execute(
                        """INSERT INTO user_vibes (thread_id, user_id, username, vibe_text, category, emoji, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(thread_id, user_id) DO UPDATE SET
                             username=excluded.username,
                             vibe_text=excluded.vibe_text,
                             category=excluded.category,
                             emoji=excluded.emoji,
                             updated_at=excluded.updated_at""",
                        (t_id, u_id, u_name, clean_text, cat, emoji, now),
                    )
            except Exception:
                pass

        cat_info = self.VIBE_CATEGORIES.get(cat, {"label": "Custom Vibe"})
        return True, (
            f"🔮 **VIBE BROADCAST UPDATED**\n"
            f"👤 @{u_name} is now: {emoji} **{clean_text}**\n"
            f"🏷️ Mood: *{cat_info['label']}*"
        )

    def get_vibe(self, thread_id: str, user_id: str, username: str, target_user: str = "") -> str:
        """Get the active vibe status for a user or target member."""
        t_id = str(thread_id)
        target_clean = target_user.lstrip("@").strip() if target_user else username.lstrip("@").strip()

        # Check in-memory first
        thread_vibes = self.vibes.get(t_id, {})
        for entry in thread_vibes.values():
            if entry.username.lower() == target_clean.lower() or (not target_user and entry.user_id == str(user_id)):
                elapsed = int(time.time() - entry.updated_at)
                m, s = divmod(elapsed, 60)
                h, m = divmod(m, 60)
                time_ago = f"{h}h {m}m ago" if h else (f"{m}m ago" if m else "just now")
                return (
                    f"🔮 **ACTIVE VIBE STATUS FOR @{entry.username}**\n"
                    f"{entry.emoji} **\"{entry.vibe_text}\"**\n"
                    f"🕒 Updated: {time_ago} • Mood: *{self.VIBE_CATEGORIES.get(entry.category, {}).get('label', 'Custom')}*"
                )

        # Fallback to random dynamic vibe oracle if not set
        vibe_archetypes = [
            ("Immaculate & Serene 🌊", "Aura is calm, collected, and vibrating on a celestial frequency."),
            ("Chaotic Good Menace ⚡", "Ready to start a group chat debate over cereal milk."),
            ("Main Character Energy 🌟", "Radiating confidence with an untouchable soundtrack in their head."),
            ("Deep Late-Night Thinker 🌌", "Contemplating the architecture of the cosmos and tomorrow's breakfast."),
            ("Overpowered & Locked In 💎", "Focus level is 9000+, unstoppable productivity mode active."),
        ]
        import hashlib
        digest = int(hashlib.md5(f"{target_clean.lower()}-{time.strftime('%Y-%m-%d')}".encode()).hexdigest(), 16)
        chosen, desc = vibe_archetypes[digest % len(vibe_archetypes)]
        return (
            f"🔮 **DAILY VIBE SCAN FOR @{target_clean}**\n"
            f"✨ Archetype: **{chosen}**\n"
            f"📖 *{desc}*\n\n"
            f"💡 *Set your real-time custom status anytime with `.vibe set <mood>`!*"
        )

    def get_vibeboard(self, thread_id: str) -> str:
        """Render group chat collective vibe board with synergy calculation."""
        t_id = str(thread_id)
        thread_vibes = self.vibes.get(t_id, {})

        if not thread_vibes and self.database is not None and hasattr(self.database, "_connect"):
            try:
                with self.database._connect() as conn:
                    rows = conn.execute(
                        "SELECT user_id, username, vibe_text, category, emoji, updated_at FROM user_vibes WHERE thread_id = ? ORDER BY updated_at DESC LIMIT 15",
                        (t_id,),
                    ).fetchall()
                    if rows:
                        self.vibes[t_id] = {}
                        for r in rows:
                            self.vibes[t_id][str(r["user_id"])] = VibeEntry(
                                user_id=str(r["user_id"]),
                                username=str(r["username"]),
                                thread_id=t_id,
                                vibe_text=str(r["vibe_text"]),
                                category=str(r["category"]),
                                emoji=str(r["emoji"]),
                                updated_at=float(r["updated_at"]),
                            )
                        thread_vibes = self.vibes[t_id]
            except Exception:
                pass

        if not thread_vibes:
            return (
                f"🔮 **GROUP CHAT VIBE BOARD**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"No active vibes set in this chat yet!\n"
                f"Set yours now with: `.vibe set <your mood/status>` 🌸"
            )

        entries = list(thread_vibes.values())
        synergy_score = min(99, 70 + (len(entries) * 7))

        lines = [
            f"🔮 **GROUP CHAT COLLECTIVE VIBE BOARD**",
            f"✨ Group Synergy: **{synergy_score}%** • Active Members: **{len(entries)}**",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]

        for e in sorted(entries, key=lambda x: x.updated_at, reverse=True)[:10]:
            lines.append(f"{e.emoji} **@{e.username}**: \"{e.vibe_text}\"")

        lines.extend([
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"💡 *Update your vibe with `.vibe set <status>`*",
        ])
        return "\n".join(lines)

    def clear_vibe(self, thread_id: str, user_id: str) -> str:
        """Clear user vibe status."""
        t_id = str(thread_id)
        u_id = str(user_id)
        if t_id in self.vibes:
            self.vibes[t_id].pop(u_id, None)

        if self.database is not None and hasattr(self.database, "_connect"):
            try:
                with self.database._connect() as conn:
                    conn.execute("DELETE FROM user_vibes WHERE thread_id = ? AND user_id = ?", (t_id, u_id))
            except Exception:
                pass

        return "✨ Your vibe status has been cleared."

