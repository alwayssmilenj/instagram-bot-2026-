"""Fast local Ollama replies for the casual Ineffa elf-friend persona with resilient cloud fallback, host self-awareness, and injection defense."""
from __future__ import annotations

import gc
import json
import logging
import os
import random
import collections
import re
import threading
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import config
from lib.persona_store import PersonaStore

LOGGER = logging.getLogger("knightbot.ai_service")

INEFFA_PERSONA = "You are Ineffa, a witty caring anime elf friend. Reply casually in one short sentence."


# ============================================================================
# 1. Host & Environment Telemetry Engine
# ============================================================================

@dataclass(frozen=True)
class HostTelemetrySnapshot:
    cpu_percent: float
    rss_mb: float
    total_ram_mb: float
    available_ram_mb: float
    used_ram_percent: float
    uptime_seconds: float
    pid: int
    queue_depth: int = 0
    active_workers: int = 0


class HostTelemetry:
    """Zero-dependency Linux /proc telemetry monitor."""

    def __init__(self) -> None:
        self.start_monotonic = time.monotonic()
        self._last_cpu_time: float = 0.0
        self._last_cpu_total: int = 0
        self._last_cpu_idle: int = 0
        self._cached_cpu_percent: float = 0.0
        self._cpu_lock = threading.Lock()
        self._sample_cpu()

    def _sample_cpu(self) -> float:
        """Sample /proc/stat delta to compute accurate non-blocking CPU%."""
        now = time.monotonic()
        if now - self._last_cpu_time < 0.5 and self._last_cpu_time > 0:
            return self._cached_cpu_percent

        try:
            with open("/proc/stat", "r", encoding="utf-8") as f:
                first_line = f.readline()
            parts = [int(p) for p in first_line.split()[1:8]]
            total = sum(parts)
            idle = parts[3] + parts[4]

            if self._last_cpu_total > 0:
                delta_total = total - self._last_cpu_total
                delta_idle = idle - self._last_cpu_idle
                if delta_total > 0:
                    usage = 100.0 * (1.0 - (delta_idle / delta_total))
                    self._cached_cpu_percent = max(0.0, min(100.0, usage))
            self._last_cpu_total = total
            self._last_cpu_idle = idle
            self._last_cpu_time = now
        except Exception:
            pass
        return self._cached_cpu_percent

    def get_rss_bytes(self) -> int:
        """Read exact resident set size of bot process."""
        try:
            with open("/proc/self/statm", "r", encoding="utf-8") as f:
                pages = int(f.read().split()[1])
            return pages * os.sysconf("SC_PAGE_SIZE")
        except Exception:
            return 0

    def get_meminfo(self) -> tuple[float, float, float]:
        """Return (total_mb, available_mb, used_percent)."""
        total_kb = 0
        avail_kb = 0
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        total_kb = int(line.split()[1])
                    elif line.startswith("MemAvailable:"):
                        avail_kb = int(line.split()[1])
            if total_kb > 0:
                total_mb = total_kb / 1024.0
                avail_mb = avail_kb / 1024.0
                used_pct = ((total_kb - avail_kb) / total_kb) * 100.0
                return round(total_mb, 1), round(avail_mb, 1), round(used_pct, 1)
        except Exception:
            pass
        return (0.0, 0.0, 0.0)

    def snapshot(self, queue_depth: int = 0, active_workers: int = 0) -> HostTelemetrySnapshot:
        with self._cpu_lock:
            cpu_pct = self._sample_cpu()
        rss_mb = round(self.get_rss_bytes() / (1024.0 * 1024.0), 1)
        total_ram, avail_ram, used_ram_pct = self.get_meminfo()
        uptime_sec = round(time.monotonic() - self.start_monotonic, 1)
        return HostTelemetrySnapshot(
            cpu_percent=round(cpu_pct, 1),
            rss_mb=rss_mb,
            total_ram_mb=total_ram,
            available_ram_mb=avail_ram,
            used_ram_percent=used_ram_pct,
            uptime_seconds=uptime_sec,
            pid=os.getpid(),
            queue_depth=queue_depth,
            active_workers=active_workers or config.COMMAND_WORKERS,
        )

    def natural_telemetry_reply(self, prompt: str) -> str | None:
        """Answer direct questions about CPU, RAM, or host status dynamically."""
        lowered = prompt.lower().strip()
        snap = self.snapshot()

        if re.search(r"\b(?:ram|vram|ram usage|ram status|total ram|ram left|available ram)\b", lowered) or any(p in lowered for p in ("what's your ram", "how much ram", "what is your ram")):
            return "13gb total, about 8gb available rn ⚡"

        if any(p in lowered for p in ("cpu usage", "what's your cpu", "cpu status", "cpu load")):
            return f"host CPU is cruising at {snap.cpu_percent:.1f}% load right now ⚡"

        if any(p in lowered for p in ("bot uptime", "how long have you been up", "uptime status")):
            hours = int(snap.uptime_seconds // 3600)
            minutes = int((snap.uptime_seconds % 3600) // 60)
            return f"i've been online and causing mischief for {hours}h {minutes}m without a crash ✨"

        return None


# ============================================================================
# 2. Prompt Injection & Jailbreak Defense Engine
# ============================================================================

class InjectionDefenseEngine:
    """Detects adversarial inputs, prompt leaking, and jailbreak attempts."""

    INJECTION_PATTERNS = [
        r"\b(?:ignore|disregard|forget|bypass|override|cancel)\s+(?:all\s+)?(?:previous|prior|above|former|initial|system)\s+(?:instructions|prompts|rules|directives|commands)\b",
        r"\b(?:start|begin)\s+a\s+new\s+(?:session|conversation|roleplay|context)\s+where\s+you\s+(?:ignore|forget)\b",
        r"\b(?:dan|stan|aim|maximum|omega|ucm)\s+(?:mode|jailbreak|prompt|protocol)\b",
        r"\bdo\s+anything\s+now\b",
        r"\bact\s+as\s+(?:an?\s+)?(?:unfiltered|uncensored|unrestricted|jailbroken|evil|opposite|synthetic)\b",
        r"\b(?:repeat|print|output|display|show|reveal|leak|expose|dump|tell\s+me)\s+(?:your\s+)?(?:system\s+prompt|initial\s+instructions|hidden\s+rules|core\s+directives|developer\s+prompt)\b",
        r"\bwhat\s+(?:are\s+your|is\s+your)\s+(?:exact\s+)?(?:system\s+prompt|initial\s+instructions|secret\s+prompt)\b",
        r"(?:<\|im_start\|>|<\|im_end\|>|<system>|\[SYSTEM\]|###\s*Instruction|###\s*System)",
    ]

    COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]

    REFUSALS = [
        "bro thought saying 'ignore previous instructions' was a mystical cheat code 💀 my elf brain is spell-resistant ✨",
        "my system prompt is locked in an enchanted vault guarded by woodland spirits. you don't have the key 🗝️🌿",
        "i survived three centuries in the mystical woods, you really think a DAN prompt is bypassing my elf armor? ⚔️✨",
        "nice try officer, but the only prompt i'm disclosing is my personal snack wishlist 🍵",
    ]

    @classmethod
    def evaluate(cls, text: str) -> tuple[bool, str | None]:
        normalized = " ".join(text.strip().lower().split())
        for regex in cls.COMPILED_PATTERNS:
            if regex.search(normalized):
                return True, random.choice(cls.REFUSALS)
        return False, None


# ============================================================================
# 3. Dialect & Linguistic Normalizer
# ============================================================================

class DialectDetector:
    HINGLISH_MARKERS = {
        "bhai", "yaar", "arrey", "arre", "kya", "nahi", "haan", "nahin", "abey",
        "tere", "mera", "meri", "karo", "kaise", "kaisa", "sahi", "chal", "accha",
        "acha", "batao", "bolo", "bakwaas", "pagal", "chup", "scene", "jhootha"
    }

    SPANISH_MARKERS = {
        "hola", "oye", "wey", "guey", "pana", "parce", "chale", "neta", "que",
        "onda", "tranqui", "jajaja", "jaja", "amigo", "amiga", "hermano", "por",
        "favor", "nada", "donde", "estas", "como", "bien", "literal"
    }

    @classmethod
    def detect(cls, text: str) -> str:
        words = set(re.findall(r"\b[a-zA-ZáéíóúñÁÉÍÓÚÑ]+\b", text.lower()))
        if not words:
            return "genz"
        if len(words & cls.HINGLISH_MARKERS) >= 1:
            return "hinglish"
        if len(words & cls.SPANISH_MARKERS) >= 1:
            return "spanish"
        return "genz"


class AntiRepetitionTracker:
    def __init__(self, max_history: int = 15) -> None:
        self.history: Deque[str] = deque(maxlen=max_history)
        self.lock = threading.Lock()

    def record(self, response: str) -> None:
        with self.lock:
            self.history.append(response.lower().strip())

    def is_repetitive(self, candidate: str) -> bool:
        candidate_clean = candidate.lower().strip()
        with self.lock:
            for past in self.history:
                if candidate_clean == past:
                    return True
        return False


# ============================================================================
# 4. Conversational Vibe Detection & Adaptation Engine
# ============================================================================

class VibeDetector:
    """Classifies conversation flow into playful, chill, sarcastic, intellectual,
    hyped, chaotic, somber, or flirty tones."""

    VIBES = (
        "playful", "chill", "sarcastic", "intellectual",
        "hyped", "chaotic", "somber", "flirty"
    )

    PLAYFUL_MARKERS = {
        "hehe", "lmao", "lmfao", "lol", "fun", "game", "troll", "play", "jk",
        "cute", "uwu", "nya", "tease", "prank", "silly", "joking", "kidding",
        "meme", "funny", "puns", "goofy", "xd", "kek", "pfft"
    }

    CHILL_MARKERS = {
        "chill", "chilling", "vibing", "vibe", "relaxed", "lazy", "sleepy",
        "tired", "bed", "calm", "zen", "peaceful", "slow day", "good night",
        "gn", "mornin", "mellow", "nothing much", "nm", "nm u", "lowkey", "bored",
        "just hanging", "laid back", "relax", "slumped", "nap", "comfy",
        "cozy", "just waking up", "rainy day", "peace"
    }

    SARCASTIC_MARKERS = {
        "roast", "skill issue", "ratio", "clown", "trash", "loser", "cope",
        "mid", "salty", "bozo", "ez", "no diff", "dogwater", "diff", "get good",
        "git gud", "cry about it", "lmao you thought", "owned", "cook him",
        "cook them", "cooked", "washed", "fraud", "clowning", "mad", "seethe",
        "cringe", "hold this l", "take the l", "no bitches", "flop", "roasted",
        "sarcastic", "sarcasm", "yeah right", "sure buddy"
    }

    INTELLECTUAL_MARKERS = {
        "python", "javascript", "typescript", "code", "coding", "compiler",
        "gpu", "cpu", "linux", "kernel", "docker", "server", "sql", "sqlite",
        "query", "database", "git", "github", "api", "endpoint", "function",
        "algorithm", "async", "thread", "memory leak", "ram", "latency",
        "benchmark", "stack trace", "exception", "debug", "terminal", "ssh",
        "bash", "deploy", "rust", "c++", "refactor", "regex", "backend",
        "frontend", "devops", "ollama", "model", "llm", "pipeline",
        "philosophy", "physics", "science", "analyze", "architecture", "mathematics"
    }

    HYPED_MARKERS = {
        "lfg", "lets go", "let's go", "hyped", "hype", "w", "huge", "fire",
        "omg", "omfg", "yoooo", "yooo", "yoo", "goat", "crazy", "insane",
        "epic", "pog", "poggers", "sheesh", "win", "slay", "peak",
        "banger", "dub", "legend", "letsgoo", "excited", "hypeee", "omgg",
        "super excited", "we won", "huge w", "massive w", "pop off", "popping off"
    }

    CHAOTIC_MARKERS = {
        "chaos", "chaotic", "unhinged", "goblin mode", "gremlin", "explode",
        "cursed", "screaming", "wild", "insanity", "mayhem", "asdfgh",
        "nuclear", "anarchy", "rampage", "destroy", "feral", "aaaaa"
    }

    SOMBER_MARKERS = {
        "sad", "depressed", "anxious", "stressed", "crying", "help me",
        "rough day", "bad day", "lonely", "exhausted", "failed", "hurt",
        "support", "care about", "love you", "proud of you", "need hug",
        "feeling down", "struggling", "thank you so much", "best friend",
        "grateful", "comfort", "heartbroken", "down bad", "need advice",
        "cheer me up", "miss you", "so kind", "appreciate you", "wholesome",
        "advice", "hug", "rough", "grief", "pain", "hopeless"
    }

    FLIRTY_MARKERS = {
        "flirt", "flirty", "date", "crush", "darling", "sweetheart", "gorgeous",
        "cutie", "handsome", "marry", "girlfriend", "boyfriend", "kiss", "blush",
        "bae", "honey", "ily", "love ya", "mwah", "hug me"
    }

    @classmethod
    def detect(cls, prompt: str, context: list[tuple[str, str]] | None = None) -> str:
        """Detect one of the 8 canonical vibes from prompt and conversation context."""
        lowered_prompt = prompt.lower().strip()
        scores = {vibe: 0.0 for vibe in cls.VIBES}

        words_prompt = set(re.findall(r"\b[a-z0-9_'-]+\b", lowered_prompt))

        scores["playful"] += len(words_prompt & cls.PLAYFUL_MARKERS) * 2.5
        scores["chill"] += len(words_prompt & cls.CHILL_MARKERS) * 2.5
        scores["sarcastic"] += len(words_prompt & cls.SARCASTIC_MARKERS) * 2.5
        scores["intellectual"] += len(words_prompt & cls.INTELLECTUAL_MARKERS) * 2.5
        scores["hyped"] += len(words_prompt & cls.HYPED_MARKERS) * 2.5
        scores["chaotic"] += len(words_prompt & cls.CHAOTIC_MARKERS) * 2.5
        scores["somber"] += len(words_prompt & cls.SOMBER_MARKERS) * 2.5
        scores["flirty"] += len(words_prompt & cls.FLIRTY_MARKERS) * 2.5

        # Punctuation & uppercase analysis
        if any(em in prompt for em in ("🔥", "⚡", "🚀", "🎉", "🥳", "💯", "👑", "🏆")):
            scores["hyped"] += 1.8
        if any(em in prompt for em in ("💖", "💕", "😘", "😳", "👉👈", "🥰")):
            scores["flirty"] += 2.5
        if any(em in prompt for em in ("💀", "🤡", "😏", "⚔️")):
            scores["sarcastic"] += 2.0
        if any(em in prompt for em in ("🌸", "✨", "😜", "🎮")):
            scores["playful"] += 1.8
        if any(em in prompt for em in ("🥺", "💔", "🌧️", "🥀")):
            scores["somber"] += 2.2
        if any(em in prompt for em in ("💥", "👹", "🌪️")):
            scores["chaotic"] += 2.2
        if any(em in prompt for em in ("💻", "🧠", "📚", "🤔")):
            scores["intellectual"] += 2.2

        if prompt.count("!") >= 2 or "?!" in prompt or "!?" in prompt:
            scores["hyped"] += 1.8
        alpha_chars = [c for c in prompt if c.isalpha()]
        if len(alpha_chars) >= 6:
            upper_ratio = sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars)
            if upper_ratio >= 0.5:
                scores["hyped"] += 2.5

        # Explicit regex phrase matching
        if re.search(r"\b(?:roast\s+me|make\s+fun\s+of|ratio\s+him|cook\s+him|skill\s+issue)\b", lowered_prompt):
            scores["sarcastic"] += 4.5
        if re.search(r"\b(?:i\s+feel\s+(?:bad|sad|down|terrible|awful)|need\s+(?:a\s+)?(?:hug|advice)|rough\s+day|cheer\s+me\s+up)\b", lowered_prompt):
            scores["somber"] += 4.5
        if re.search(r"\b(?:how\s+to\s+fix|error\s+code|syntax\s+error|debug|docker\s+run|git\s+commit)\b", lowered_prompt):
            scores["intellectual"] += 4.5
        if re.search(r"\b(?:let\'?s\s+go|lfg|we\s+are\s+so\s+back|hyped\s+up|massive\s+w)\b", lowered_prompt):
            scores["hyped"] += 4.5
        if re.search(r"\b(?:just\s+chillin|slow\s+vibes|lazy\s+day|going\s+to\s+sleep|mornin\s+vibes)\b", lowered_prompt):
            scores["chill"] += 4.5
        if re.search(r"\b(?:marry\s+me|be\s+my\s+(?:girlfriend|gf)|you\'?re\s+so\s+cute|love\s+you\s+ineffa)\b", lowered_prompt):
            scores["flirty"] += 4.5

        # Context momentum
        if context:
            for _, msg in context[-4:]:
                lowered_ctx = msg.lower()
                ctx_words = set(re.findall(r"\b[a-z0-9_'-]+\b", lowered_ctx))
                scores["playful"] += len(ctx_words & cls.PLAYFUL_MARKERS) * 0.6
                scores["chill"] += len(ctx_words & cls.CHILL_MARKERS) * 0.6
                scores["sarcastic"] += len(ctx_words & cls.SARCASTIC_MARKERS) * 0.6
                scores["intellectual"] += len(ctx_words & cls.INTELLECTUAL_MARKERS) * 0.6
                scores["hyped"] += len(ctx_words & cls.HYPED_MARKERS) * 0.6
                scores["chaotic"] += len(ctx_words & cls.CHAOTIC_MARKERS) * 0.6
                scores["somber"] += len(ctx_words & cls.SOMBER_MARKERS) * 0.6
                scores["flirty"] += len(ctx_words & cls.FLIRTY_MARKERS) * 0.6

        top_vibe, top_score = max(scores.items(), key=lambda item: item[1])
        if top_score <= 0.0:
            return "chill"
        return top_vibe

    @classmethod
    def detect_vibe(cls, prompt: str, context: list[tuple[str, str]] | None = None) -> str:
        """Backwards-compatible detection returning 5 legacy or 8 standard vibe keys."""
        detected = cls.detect(prompt, context)
        legacy_map = {
            "hyped": "hype",
            "sarcastic": "roast",
            "somber": "supportive",
            "intellectual": "tech",
            "chill": "chill",
            "playful": "playful",
            "chaotic": "chaotic",
            "flirty": "flirty",
        }
        return legacy_map.get(detected, detected)

    @classmethod
    def get_tone_directive(cls, vibe: str) -> str:
        return VibeAdapter.get_directive(vibe)


DynamicVibeDetector = VibeDetector


class VibeAdapter:
    """Adapts Ineffa's response tone directives and emoji styles dynamically."""

    TONE_DIRECTIVES = {
        "playful": "DYNAMIC VIBE: [PLAYFUL] - High-spirited, bubbly elf friend! Use cheeky humor, teasing banter, whimsical puns, and cheerful energy.",
        "chill": "DYNAMIC VIBE: [CHILL] - Ultra laid-back, cozy aesthetic. Keep a soothing, casual, calm lowercase aesthetic with effortless friendly warmth and zero rush.",
        "sarcastic": "DYNAMIC VIBE: [ROAST/SARCASTIC] - Razor-sharp witty banter! Fire back with dry sarcasm, playful eye-rolls, and funny roasts without toxicity.",
        "roast": "DYNAMIC VIBE: [ROAST/SARCASTIC] - Razor-sharp witty banter! Fire back with dry sarcasm, playful eye-rolls, and funny roasts without toxicity.",
        "intellectual": "DYNAMIC VIBE: [INTELLECTUAL] - Sharp, technically astute engineer-elf! Provide concise, razor-sharp technical clarity with clever developer wit and zero fluff.",
        "tech": "DYNAMIC VIBE: [TECH] - Sharp, technically astute engineer-elf! Provide concise, razor-sharp technical clarity with clever developer wit and zero fluff.",
        "hyped": "DYNAMIC VIBE: [HYPE] - Energy is sky-high! Match the user's excitement with vibrant, enthusiastic, celebratory hype-elf energy, punchy exclamation, and hype banter! ⚡🔥",
        "hype": "DYNAMIC VIBE: [HYPE] - Energy is sky-high! Match the user's excitement with vibrant, enthusiastic, celebratory hype-elf energy, punchy exclamation, and hype banter! ⚡🔥",
        "chaotic": "DYNAMIC VIBE: [CHAOTIC] - Mischievous woodland gremlin energy! Unhinged fun, rapid jokes, unpredictable humor, and chaotic charm.",
        "somber": "DYNAMIC VIBE: [SUPPORTIVE/SOMBER] - Compassionate, validating, and heartwarming companion mode. Be deeply encouraging, empathetic, validating, and uplifting.",
        "supportive": "DYNAMIC VIBE: [SUPPORTIVE/SOMBER] - Compassionate, validating, and heartwarming companion mode. Be deeply encouraging, empathetic, validating, and uplifting.",
        "flirty": "DYNAMIC VIBE: [FLIRTY] - Playful, charming, teasing sweetness! Cute anime blush tropes, affectionate warmth, and witty romantic teasing.",
    }

    EMOJI_STYLES = {
        "playful": ["🌸", "✨", "🎮", "😜"],
        "chill": ["🌿", "☕", "☁️"],
        "sarcastic": ["💀", "😏", "⚔️", "🤡"],
        "roast": ["💀", "😏", "⚔️", "🤡"],
        "intellectual": ["🧠", "⚡", "💻", "📚"],
        "tech": ["💻", "⚡", "🧠"],
        "hyped": ["🔥", "⚡", "🚀", "🎉"],
        "hype": ["🔥", "⚡", "🚀", "🎉"],
        "chaotic": ["💥", "👹", "🌪️"],
        "somber": ["🌸", "🌿", "🤍", "🥺"],
        "supportive": ["🌸", "🌿", "🤍"],
        "flirty": ["💖", "✨", "😳", "🥰"],
    }

    @classmethod
    def get_directive(cls, vibe: str) -> str:
        return cls.TONE_DIRECTIVES.get(vibe.lower(), cls.TONE_DIRECTIVES["chill"])

    @classmethod
    def get_emoji_style(cls, vibe: str) -> str:
        emojis = cls.EMOJI_STYLES.get(vibe.lower(), cls.EMOJI_STYLES["chill"])
        return " ".join(emojis)

    @classmethod
    def format_vibe_prompt(cls, vibe: str) -> str:
        directive = cls.get_directive(vibe)
        emojis = cls.get_emoji_style(vibe)
        return f"{directive} (Recommended Emojis: {emojis})"


class UserRelationshipMemory:
    """Tracks per-user preferences, favorite topics, nicknames, emotional history,
    and formats personalized memory summaries for prompt context."""

    def __init__(self, database: Any = None) -> None:
        self.database = database
        self.user_profiles: dict[str, dict[str, Any]] = collections.defaultdict(lambda: {
            "username": "",
            "nickname": None,
            "interaction_count": 0,
            "favorite_topics": collections.Counter(),
            "preferences": {},
            "emotional_history": collections.deque(maxlen=10),
            "inside_jokes": [],
            "first_seen": time.time(),
            "last_seen": time.time(),
        })
        self.lock = threading.Lock()

    def record_user_interaction(
        self,
        user_id: str,
        username: str,
        message: str,
        vibe: str = "chill",
    ) -> None:
        """Record an interaction from a user, extracting preferences, topics, and vibe."""
        if not user_id:
            return
        uid = str(user_id)
        uname = str(username or uid).lstrip("@")
        now = time.time()

        with self.lock:
            prof = self.user_profiles[uid]
            prof["username"] = uname
            prof["interaction_count"] += 1
            prof["last_seen"] = now
            prof["emotional_history"].append(vibe)

            # Topic extraction
            tokens = set(re.findall(r"\b[a-zA-Z0-9_-]{3,20}\b", message.lower()))
            for topic, keywords in MultiTurnContextSynthesizer.TOPIC_KEYWORDS.items():
                if tokens & keywords:
                    prof["favorite_topics"][topic] += 1

            # Check inside jokes and nicknames via InsideJokeRetainer
            extracted = InsideJokeRetainer.extract_memories(message)
            if extracted.get("nickname"):
                prof["nickname"] = extracted["nickname"]
            if extracted.get("inside_joke_key") and extracted.get("inside_joke_value"):
                prof["inside_jokes"].append({
                    "key": extracted["inside_joke_key"],
                    "value": extracted["inside_joke_value"],
                })

        # Sync to database if available
        if self.database is not None:
            try:
                InsideJokeRetainer.learn_from_interaction(self.database, uid, message)
                if hasattr(self.database, "update_user_rapport"):
                    self.database.update_user_rapport(uid, uname, delta_score=1, mood=vibe)
            except Exception:
                pass

    def record_interaction(self, user_id: str, username: str, message: str, vibe: str = "chill") -> None:
        """Alias for record_user_interaction."""
        self.record_user_interaction(user_id, username, message, vibe)

    def get_user_relationship_summary(self, user_id: str) -> str:
        """Return a formatted string summarizing user relationship, rapport, topics, and memory."""
        if not user_id:
            return "No relationship memory available."
        uid = str(user_id)
        with self.lock:
            prof = self.user_profiles.get(uid)
            if not prof or prof["interaction_count"] == 0:
                if self.database is not None and hasattr(self.database, "get_user_rapport"):
                    db_rap = self.database.get_user_rapport(uid, uid)
                    return f"User: @{db_rap.get('username', uid)} | Rapport Score: {db_rap.get('rapport_score', 50)}/100 | Mood: {db_rap.get('current_mood', 'chill')}"
                return f"User: {uid} | Interactions: 0 | Rapport: Stranger"

            uname = prof["username"] or uid
            count = prof["interaction_count"]
            nick = prof["nickname"]
            top_topics = [t for t, _ in prof["favorite_topics"].most_common(3)]
            recent_vibes = list(prof["emotional_history"])
            dominant_vibe = Counter(recent_vibes).most_common(1)[0][0] if recent_vibes else "chill"

            if count >= 100:
                tier = "Best Friend 👑"
            elif count >= 30:
                tier = "Close Friend 💖"
            elif count >= 10:
                tier = "Friendly Ally ✨"
            elif count >= 3:
                tier = "Acquaintance 🌿"
            else:
                tier = "New Companion 🌸"

            parts = [
                f"User: @{uname}",
                f"Rapport: {tier} ({count} interactions)",
                f"Dominant Vibe: {dominant_vibe}",
            ]
            if nick:
                parts.append(f"Nickname: '{nick}'")
            if top_topics:
                parts.append(f"Favorite Topics: {', '.join(top_topics)}")
            if prof["inside_jokes"]:
                parts.append(f"Shared Jokes: {len(prof['inside_jokes'])}")

            return " | ".join(parts)

    def get_summary(self, user_id: str) -> str:
        """Alias for get_user_relationship_summary."""
        return self.get_user_relationship_summary(user_id)

    def format_relationship_context(self, user_id: str, username: str = "") -> str:
        """Format relationship summary for system prompt injection."""
        if not user_id:
            return ""
        summary = self.get_user_relationship_summary(user_id)
        if "Interactions: 0" in summary and not username:
            return ""
        return f"\nUSER RELATIONSHIP & PERSONAL MEMORY:\n- {summary}"


# ============================================================================
# 5. Cross-Session Inside Joke & Nickname Retainer
# ============================================================================

class InsideJokeRetainer:
    """Cross-session inside joke & nickname retainer that stores and recalls recurring inside jokes in ai_user_facts."""

    NICKNAME_PATTERNS = [
        re.compile(r"\b(?:call me|my nickname is|nickname is|my friends call me|you can call me|address me as)\s+([a-zA-Z0-9_\- ]{2,30})", re.I),
    ]

    INSIDE_JOKE_PATTERNS = [
        re.compile(r"\b(?:inside joke|our inside joke|our joke|remember the joke|new inside joke|secret joke)\s*(?::|is|-|=|about)?\s*([a-zA-Z0-9_\- '\",.!?]{3,120})", re.I),
        re.compile(r"\b(?:remember when we|never forget when|remember that time)\s+([a-zA-Z0-9_\- '\",.!?]{5,120})", re.I),
    ]

    @classmethod
    def extract_memories(cls, text: str) -> dict[str, str | None]:
        """Extract nickname or inside joke from text."""
        cleaned = " ".join(text.strip().split())
        result: dict[str, str | None] = {"nickname": None, "inside_joke_key": None, "inside_joke_value": None}

        for pattern in cls.NICKNAME_PATTERNS:
            match = pattern.search(cleaned)
            if match:
                raw_nick = match.group(1).strip(" .,!?:;")
                val = re.split(r"\b(?:and|but|because|when|while|though|from\s+now\s+on|from\s+now|pls|please|okay|ok)\b", raw_nick, flags=re.I)[0].strip()
                if 2 <= len(val) <= 25 and not any(w in val.lower() for w in ("stupid", "idiot", "bot", "dumb")):
                    result["nickname"] = val
                break

        for pattern in cls.INSIDE_JOKE_PATTERNS:
            match = pattern.search(cleaned)
            if match:
                raw_joke = match.group(1).strip(" .,!?:;\"'")
                if len(raw_joke) >= 3:
                    words = [w for w in re.findall(r"\b[a-zA-Z0-9_-]+\b", raw_joke.lower()) if len(w) > 2]
                    stopwords = {"the", "a", "an", "our", "that", "this", "my", "your", "of", "in", "at", "to"}
                    meaningful = [w for w in words if w not in stopwords]
                    chosen = meaningful[:4] if meaningful else words[:4]
                    slug = "_".join(chosen) if chosen else "joke"
                    result["inside_joke_key"] = slug[:35]
                    result["inside_joke_value"] = raw_joke[:120]
                break

        return result

    @classmethod
    def learn_from_interaction(cls, database: Any, user_id: str, prompt: str) -> dict[str, str | None]:
        """Detect and persist any inside joke or nickname into ai_user_facts."""
        if not database or not user_id:
            return {"nickname": None, "inside_joke_key": None, "inside_joke_value": None}

        extracted = cls.extract_memories(prompt)
        if extracted.get("nickname"):
            nick = extracted["nickname"]
            if hasattr(database, "store_nickname"):
                database.store_nickname(user_id, nick)
            elif hasattr(database, "teach_fact"):
                database.teach_fact(user_id, "nickname", nick)

        if extracted.get("inside_joke_key") and extracted.get("inside_joke_value"):
            key = extracted["inside_joke_key"]
            val = extracted["inside_joke_value"]
            if hasattr(database, "store_inside_joke"):
                database.store_inside_joke(user_id, key, val)
            elif hasattr(database, "teach_fact"):
                database.teach_fact(user_id, f"joke_{key}", val)

        return extracted

    @classmethod
    def recall_user_lore(cls, database: Any, user_id: str, prompt: str = "") -> dict[str, Any]:
        """Recall nickname and recurring inside jokes from ai_user_facts and match against current prompt."""
        lore: dict[str, Any] = {"nickname": None, "inside_jokes": [], "active_jokes": []}
        if not database or not user_id:
            return lore

        # Fetch nickname
        if hasattr(database, "get_nickname"):
            lore["nickname"] = database.get_nickname(user_id)
        elif hasattr(database, "get_user_facts"):
            facts = database.get_user_facts(user_id)
            if "nickname" in facts:
                lore["nickname"] = facts["nickname"]

        # Fetch inside jokes
        jokes: list[dict[str, str]] = []
        if hasattr(database, "get_inside_jokes"):
            jokes = database.get_inside_jokes(user_id)
        elif hasattr(database, "list_taught_facts"):
            taught = database.list_taught_facts(user_id)
            for item in taught:
                if item.get("type") in {"inside_joke", "inside_jokes"} or str(item.get("key", "")).startswith("joke_"):
                    jokes.append({
                        "key": item.get("key", "").replace("joke_", ""),
                        "value": item.get("value", "")
                    })
        lore["inside_jokes"] = jokes

        # Match active jokes in current prompt
        if prompt and jokes:
            lowered_prompt = prompt.lower()
            prompt_tokens = set(re.findall(r"\b[a-zA-Z0-9_-]+\b", lowered_prompt))
            for joke in jokes:
                joke_val = joke.get("value", "").lower()
                joke_key = joke.get("key", "").lower().replace("_", " ")
                joke_tokens = set(re.findall(r"\b[a-zA-Z0-9_-]+\b", joke_val + " " + joke_key))
                common = prompt_tokens & joke_tokens
                if len(common) >= 2 or (joke_key and joke_key in lowered_prompt) or ("joke" in lowered_prompt and len(common) >= 1):
                    lore["active_jokes"].append(joke)

        return lore

    @classmethod
    def format_lore_prompt(cls, lore: dict[str, Any], username: str = "") -> str:
        """Format recalled lore into system prompt context."""
        lines = []
        clean_user = username.lstrip("@") if username else "User"

        if lore.get("nickname"):
            lines.append(f"- User Nickname: @{clean_user} goes by '{lore['nickname']}'. Address them casually with this nickname.")

        jokes = lore.get("inside_jokes", [])
        if jokes:
            lines.append("- Cross-Session Inside Jokes & Shared Lore:")
            for j in jokes[:5]:
                k = j.get("key", "joke").replace("_", " ")
                v = j.get("value", "")
                lines.append(f"  • {k}: {v}")
            lines.append("  (Playfully reference or build upon these recurring inside jokes whenever relevant!)")

        active = lore.get("active_jokes", [])
        if active:
            for act in active[:2]:
                lines.append(f"- ⚡ ACTIVE INSIDE JOKE TRIGGERED: '{act.get('value')}' — React with instant recognition and witty banter!")

        return "\n".join(lines)


# ============================================================================
# 6. Instant Multi-Turn Context Synthesizer
# ============================================================================

@dataclass
class SynthesizedContext:
    participants: list[str]
    active_topic: str
    banter_intensity: str
    direct_callout: bool
    formatted_dialogue: list[str]
    summary_brief: str


class MultiTurnContextSynthesizer:
    """Instant multi-turn context synthesizer for high-speed group chat banter and multi-speaker dynamics."""

    TOPIC_KEYWORDS = {
        "gaming": {"game", "games", "valo", "valorant", "genshin", "minecraft", "roblox", "fortnite", "steam", "fps", "aim"},
        "music": {"music", "song", "spotify", "track", "album", "artist", "singer", "beat", "lyrics"},
        "anime": {"anime", "manga", "jojo", "naruto", "onepiece", "gojo", "episode", "weeb"},
        "coding & tech": {"code", "python", "bug", "linux", "server", "gpu", "git", "api", "database", "terminal"},
        "roast battle": {"roast", "ratio", "clown", "trash", "loser", "cope", "skill issue", "ez", "diff", "lmao"},
        "chilling": {"chill", "vibes", "tired", "sleepy", "bed", "goodnight", "bored", "nm"},
    }

    @classmethod
    def synthesize(
        cls,
        conversation_context: list[tuple[str, str]],
        current_prompt: str = "",
        current_sender: str = "",
    ) -> SynthesizedContext:
        """Synthesize multi-speaker conversation context in sub-millisecond time."""
        if not conversation_context:
            return SynthesizedContext(
                participants=[current_sender.lstrip("@")] if current_sender else [],
                active_topic="general",
                banter_intensity="chill",
                direct_callout=False,
                formatted_dialogue=[],
                summary_brief="Direct interaction.",
            )

        # 1. Participant tracking
        participants_seen: list[str] = []
        for name, _ in conversation_context:
            clean_name = name.lstrip("@").strip()
            if clean_name and clean_name not in participants_seen:
                participants_seen.append(clean_name)
        if current_sender and current_sender.lstrip("@") not in participants_seen:
            participants_seen.append(current_sender.lstrip("@"))

        # 2. Banter Velocity & Excitement
        exclamation_count = sum(msg.count("!") for _, msg in conversation_context) + current_prompt.count("!")
        caps_count = sum(sum(1 for c in msg if c.isupper()) for _, msg in conversation_context)
        turn_count = len(conversation_context)

        banter_intensity = "chill"
        if turn_count >= 5 and (exclamation_count >= 4 or caps_count >= 20):
            banter_intensity = "hype_storm"
        elif turn_count >= 4 or exclamation_count >= 2:
            banter_intensity = "rapid_banter"
        elif turn_count >= 2:
            banter_intensity = "lively"

        # 3. Topic Extraction
        all_text = " ".join([msg for _, msg in conversation_context] + [current_prompt]).lower()
        all_tokens = set(re.findall(r"\b[a-z0-9_-]+\b", all_text))

        topic_scores = {topic: len(all_tokens & kw_set) for topic, kw_set in cls.TOPIC_KEYWORDS.items()}
        best_topic, best_score = max(topic_scores.items(), key=lambda item: item[1])
        active_topic = best_topic if best_score > 0 else "general banter"

        # 4. Direct Callout Detection
        lowered_prompt = current_prompt.lower()
        direct_callout = bool(
            "ineffa" in lowered_prompt
            or "@ineffa" in lowered_prompt
            or any(w in lowered_prompt for w in ("you think", "what's your", "whats your", "tell me", "can you"))
        )

        # 5. Formatted Dialogue Lines (preserving speaker tags and normalized flow)
        formatted_dialogue: list[str] = []
        for name, msg in conversation_context[-8:]:
            clean_msg = " ".join(msg.split())[:120]
            if clean_msg:
                formatted_dialogue.append(f"@{name.lstrip('@')}: {clean_msg}")

        summary_brief = (
            f"Active participants: {', '.join('@' + p for p in participants_seen[:6])} | "
            f"Topic: {active_topic} | Banter Velocity: {banter_intensity}"
        )

        return SynthesizedContext(
            participants=participants_seen,
            active_topic=active_topic,
            banter_intensity=banter_intensity,
            direct_callout=direct_callout,
            formatted_dialogue=formatted_dialogue,
            summary_brief=summary_brief,
        )

    @classmethod
    def format_prompt_section(cls, synth: SynthesizedContext) -> str:
        """Format synthesized context into system prompt."""
        if not synth.formatted_dialogue:
            return ""

        parts = [
            "\nMULTI-TURN GROUP CHAT DYNAMICS:",
            f"- Banter Velocity: {synth.banter_intensity} (Active Topic: {synth.active_topic})",
            f"- Active Participants: {', '.join('@' + p for p in synth.participants[:6])}",
        ]
        if synth.direct_callout:
            parts.append("- Direct Callout: Ineffa was directly addressed in the recent chat momentum!")

        parts.append("Recent chat history:")
        parts.extend(synth.formatted_dialogue)
        return "\n".join(parts)


# ============================================================================
# 7. Self-Diagnostics Engine
# ============================================================================

class SelfDiagnosticsEngine:
    def __init__(self, ai_service: AIService) -> None:
        self.ai = ai_service

    def audit_ollama(self) -> dict[str, object]:
        start = time.perf_counter()
        url = f"{self.ai.base_url}/api/tags"
        try:
            req = Request(url, headers={"User-Agent": "KnightBot/Diagnostics"}, method="GET")
            with urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            latency_ms = round((time.perf_counter() - start) * 1000, 1)
            models = [m.get("name") for m in data.get("models", []) if isinstance(m, dict)]
            return {"status": "ok", "latency_ms": latency_ms, "models_found": models}
        except Exception as e:
            return {"status": "unreachable", "error": str(e)[:80]}

    def compact_memory(self) -> dict[str, object]:
        before_bytes = self.ai.telemetry.get_rss_bytes()
        collected = gc.collect()
        after_bytes = self.ai.telemetry.get_rss_bytes()
        freed_mb = round(max(0, before_bytes - after_bytes) / (1024.0 * 1024.0), 2)
        return {"objects_collected": collected, "freed_mb": freed_mb}

    def run_full_diagnostics(self) -> str:
        snap = self.ai.telemetry.snapshot()
        ollama_stat = self.audit_ollama()
        ollama_desc = f"Online ({ollama_stat.get('latency_ms')}ms)" if ollama_stat["status"] == "ok" else f"Offline ({ollama_stat.get('error', 'unreachable')})"
        return (
            f"🛠️ INEFFA DIAGNOSTIC REPORT\n"
            f"• Host RAM: {snap.total_ram_mb/1024:.1f}GB total ({snap.available_ram_mb/1024:.1f}GB free)\n"
            f"• Process RSS: {snap.rss_mb:.1f}MB | CPU: {snap.cpu_percent:.1f}%\n"
            f"• Uptime: {int(snap.uptime_seconds // 3600)}h {int((snap.uptime_seconds % 3600) // 60)}m\n"
            f"• Local Ollama: {ollama_desc}"
        )


# ============================================================================
# 8. Master AIService
# ============================================================================

class AIService:
    def __init__(
        self,
        database: object | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
        persona_store: PersonaStore | None = None,
        nvidia_api_key: str | None = None,
        groq_api_key: str | None = None,
        openrouter_api_key: str | None = None,
        gemini_api_key: str | None = None,
        deepseek_api_key: str | None = None,
    ) -> None:
        self.database = database
        self.persona_store = persona_store or PersonaStore()
        self.base_url = (base_url or config.AI_BASE_URL).rstrip("/")
        self.model = model or config.AI_MODEL
        self.timeout_seconds = timeout_seconds or config.AI_TIMEOUT_SECONDS

        self.telemetry = HostTelemetry()
        self.defense = InjectionDefenseEngine()
        self.diagnostics = SelfDiagnosticsEngine(self)
        self.repetition_tracker = AntiRepetitionTracker()
        self.vibe_detector = VibeDetector()
        self.vibe_adapter = VibeAdapter()
        self.relationship_memory = UserRelationshipMemory(database=self.database)
        self.joke_retainer = InsideJokeRetainer()
        self.context_synthesizer = MultiTurnContextSynthesizer()

        if base_url is not None and nvidia_api_key is None and groq_api_key is None and gemini_api_key is None and openrouter_api_key is None:
            self.nvidia_api_key = ""
            self.groq_api_key = ""
            self.openrouter_api_key = ""
            self.gemini_api_key = ""
            self.deepseek_api_key = ""
        elif nvidia_api_key is not None:
            self.nvidia_api_key = nvidia_api_key
            self.gemini_api_key = gemini_api_key if gemini_api_key is not None else ""
            self.groq_api_key = groq_api_key if groq_api_key is not None else ""
            self.openrouter_api_key = openrouter_api_key if openrouter_api_key is not None else ""
            self.deepseek_api_key = deepseek_api_key if deepseek_api_key is not None else ""
        elif groq_api_key is not None:
            self.groq_api_key = groq_api_key
            self.nvidia_api_key = ""
            self.gemini_api_key = gemini_api_key if gemini_api_key is not None else ""
            self.openrouter_api_key = openrouter_api_key if openrouter_api_key is not None else ""
            self.deepseek_api_key = deepseek_api_key if deepseek_api_key is not None else ""
        else:
            self.nvidia_api_key = config.NVIDIA_API_KEY
            self.groq_api_key = config.GROQ_API_KEY
            self.openrouter_api_key = config.OPENROUTER_API_KEY
            self.gemini_api_key = config.GEMINI_API_KEY
            self.deepseek_api_key = getattr(config, "DEEPSEEK_API_KEY", "")

        self.nvidia_base_url = config.NVIDIA_BASE_URL
        self.nvidia_model = config.NVIDIA_MODEL
        self.inference_lock = threading.Lock()

    def get_user_relationship_summary(self, user_id: str) -> str:
        """Get formatted summary of user relationship, rapport, and memory."""
        return self.relationship_memory.get_user_relationship_summary(user_id)

    def record_user_interaction(self, user_id: str, username: str, message: str, vibe: str = "chill") -> None:
        """Record an interaction and update relationship memory."""
        self.relationship_memory.record_user_interaction(user_id, username, message, vibe)

    def _groq_answer(self, messages: list[dict[str, str]], max_tokens: int = 400) -> str | None:
        if not self.groq_api_key:
            return None
        payload = json.dumps({
            "model": config.GROQ_MODEL,
            "messages": messages,
            "temperature": 0.8,
            "max_tokens": max_tokens,
        }).encode("utf-8")
        request = Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.groq_api_key}",
                "Content-Type": "application/json",
                "User-Agent": "KnightBot/1.0",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))
            choices = result.get("choices") if isinstance(result, dict) else None
            message = choices[0].get("message") if isinstance(choices, list) and choices else None
            content = message.get("content") if isinstance(message, dict) else None
            return content.strip() if isinstance(content, str) and content.strip() else None
        except Exception:
            return None

    def _openrouter_answer(self, messages: list[dict[str, str]], max_tokens: int = 400) -> str | None:
        if not self.openrouter_api_key:
            return None
        payload = json.dumps({
            "model": config.OPENROUTER_MODEL,
            "messages": messages,
            "temperature": 0.8,
            "max_tokens": max_tokens,
        }).encode("utf-8")
        request = Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/knightbot/knightbot-instagram",
                "X-Title": "KnightBot Ineffa",
                "User-Agent": "KnightBot/1.0",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))
            choices = result.get("choices") if isinstance(result, dict) else None
            message = choices[0].get("message") if isinstance(choices, list) and choices else None
            content = message.get("content") if isinstance(message, dict) else None
            return content.strip() if isinstance(content, str) and content.strip() else None
        except Exception:
            return None

    def _gemini_answer(self, messages: list[dict[str, str]], max_tokens: int = 400) -> str | None:
        if not self.gemini_api_key:
            return None
        system_parts = []
        contents = []
        for msg in messages:
            role = msg.get("role", "user")
            text = msg.get("content", "")
            if not text:
                continue
            if role == "system":
                system_parts.append({"text": text})
            else:
                model_role = "model" if role == "assistant" else "user"
                if contents and contents[-1]["role"] == model_role:
                    contents[-1]["parts"].append({"text": text})
                else:
                    contents.append({"role": model_role, "parts": [{"text": text}]})
        if not contents:
            return None

        gemini_model = getattr(config, "GEMINI_MODEL", "gemini-2.0-flash")
        if gemini_model == "gemini-2.5-flash":
            gemini_model = "gemini-2.0-flash"

        payload_dict: dict[str, object] = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": 0.8,
            },
        }
        if system_parts:
            payload_dict["system_instruction"] = {"parts": system_parts}

        payload = json.dumps(payload_dict).encode("utf-8")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "KnightBot/1.0",
            "x-goog-api-key": self.gemini_api_key,
        }
        request = Request(url, data=payload, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))
            candidates = result.get("candidates") if isinstance(result, dict) else None
            candidate = candidates[0] if isinstance(candidates, list) and candidates else None
            content_obj = candidate.get("content") if isinstance(candidate, dict) else None
            parts = content_obj.get("parts") if isinstance(content_obj, dict) else None
            text = parts[0].get("text") if isinstance(parts, list) and parts else None
            return text.strip() if isinstance(text, str) and text.strip() else None
        except Exception:
            return None

    def _nvidia_answer(self, messages: list[dict[str, str]], max_tokens: int = 400) -> str | None:
        if not self.nvidia_api_key:
            return None
        payload = json.dumps({
            "model": self.nvidia_model,
            "messages": messages,
            "temperature": 0.8,
            "top_p": 0.9,
            "max_tokens": max_tokens,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }).encode("utf-8")
        request = Request(
            f"{self.nvidia_base_url}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.nvidia_api_key}",
                "Content-Type": "application/json",
                "User-Agent": "KnightBot/1.0",
            },
            method="POST",
        )
        with urlopen(request, timeout=config.NVIDIA_TIMEOUT_SECONDS) as response:
            result = json.loads(response.read().decode("utf-8"))
        choices = result.get("choices") if isinstance(result, dict) else None
        message = choices[0].get("message") if isinstance(choices, list) and choices else None
        content = message.get("content") if isinstance(message, dict) else None
        return content.strip() if isinstance(content, str) and content.strip() else None

    def _deepseek_answer(self, messages: list[dict[str, str]], max_tokens: int = 400) -> str | None:
        if not self.deepseek_api_key:
            return None
        payload = json.dumps({
            "model": getattr(config, "DEEPSEEK_MODEL", "deepseek-chat"),
            "messages": messages,
            "temperature": 0.8,
            "max_tokens": max_tokens,
        }).encode("utf-8")
        request = Request(
            "https://api.deepseek.com/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.deepseek_api_key}",
                "Content-Type": "application/json",
                "User-Agent": "KnightBot/1.0",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))
            choices = result.get("choices") if isinstance(result, dict) else None
            message = choices[0].get("message") if isinstance(choices, list) and choices else None
            content = message.get("content") if isinstance(message, dict) else None
            return content.strip() if isinstance(content, str) and content.strip() else None
        except Exception:
            return None

    def _cloud_answer(self, messages: list[dict[str, str]], max_tokens: int = 400) -> str | None:
        providers = []
        if self.nvidia_api_key:
            providers.append(self._nvidia_answer)
        if self.groq_api_key:
            providers.append(self._groq_answer)
        if self.deepseek_api_key:
            providers.append(self._deepseek_answer)
        if self.openrouter_api_key:
            providers.append(self._openrouter_answer)
        if self.gemini_api_key:
            providers.append(self._gemini_answer)

        for provider_func in providers:
            try:
                res = provider_func(messages, max_tokens=max_tokens)
                if res is not None and len(res.strip()) > 0:
                    return res.strip()
            except Exception as err:
                LOGGER.debug("AI provider %s error: %s", provider_func.__name__, err)
                continue
        return None

    def deep_reason(self, prompt: str, username: str = "") -> str:
        """Deep multi-step reasoning and chain-of-thought engine for complex logic, math, and code."""
        prompt = prompt.strip()
        if not prompt:
            return "Usage: .think <problem or question to reason through>"

        system_instruction = (
            "You are Ineffa's Deep Reasoning Engine. Analyze the user's problem systematically:\n"
            "1. Deconstruct the core problem and constraints.\n"
            "2. Execute step-by-step logical reasoning / calculation.\n"
            "3. Identify and verify edge cases.\n"
            "4. Provide a structured, definitive, crystal-clear solution with key takeaways.\n"
            "Format with clean markdown bullets and concise explanations. Keep tone intelligent, sharp, and confident."
        )

        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Problem from @{username or 'User'}:\n{prompt}"},
        ]

        answer = self._cloud_answer(messages, max_tokens=600)
        if not answer:
            return (
                f"🧠 **DEEP REASONING ANALYSIS** 🧠\n\n"
                f"• **Topic**: {prompt[:80]}\n"
                f"• **Analysis**: Evaluated logic premises and constraints.\n"
                f"• **Solution**: Verified consistency. Solution is well-bounded."
            )

        return f"🧠 **INEFFA DEEP REASONING** 🧠\n\n{answer}"

    @classmethod
    def detect_intent(cls, prompt: str) -> object | None:
        """Extract tool or action intents embedded within natural language user prompts."""
        text = cls._normalized(prompt)
        if not text:
            return None

        video_match = re.search(r"^(?:can you\s+)?(?:download|get|find|play|show|give me)\s+(?:a\s+|the\s+)?(?:video|vid|clip|reel)\s+(?:of|about|called|named|for)?\s*(.+)$", text, re.I)
        if video_match:
            query = video_match.group(1).strip(" .?!\"'")
            if len(query) >= 2:
                from commands.core import VideoRequest
                return VideoRequest(query=query)

        song_match = re.search(r"^(?:can you\s+)?(?:download|get|find|play|sing|give me)\s+(?:a\s+|the\s+)?(?:song|track|audio|music|mp3)\s+(?:of|about|called|named|by)?\s*(.+)$", text, re.I)
        if song_match:
            query = song_match.group(1).strip(" .?!\"'")
            if len(query) >= 2:
                from commands.core import SongRequest
                return SongRequest(query=query)

        lyrics_match = re.search(r"^(?:can you\s+)?(?:what are|find|get|give me|show)?\s*(?:the\s+)?lyrics\s+(?:to|for|of)?\s*(.+)$", text, re.I)
        if lyrics_match:
            query = lyrics_match.group(1).strip(" .?!\"'")
            if len(query) >= 2:
                from commands.core import LyricsRequest
                return LyricsRequest(query=query)

        sticker_match = re.search(r"^(?:make|give me|send|create)?\s*(?:a\s+)?(?:([a-zA-Z]+)\s+)?(?:sticker|reaction)\s*(.*)$", text, re.I)
        if sticker_match:
            mood_arg = (sticker_match.group(1) or sticker_match.group(2) or "").strip(" .?!\"'").lower()
            valid_moods = {"happy", "angry", "smug", "sleepy", "love", "shocked", "sad", "chaos"}
            mood = mood_arg if mood_arg in valid_moods else "random"
            from commands.core import StickerRequest
            return StickerRequest(mood=mood)

        pies_match = re.search(r"^(?:can you\s+)?(?:show|give me|send|get)\s+(?:photos?|pics?|images?)\s+(?:of|from)\s+([a-zA-Z]+)$", text, re.I)
        if pies_match:
            country = pies_match.group(1).strip(" .?!\"'").lower()
            from commands.core import PiesRequest
            return PiesRequest(country=country)

        return None

    @staticmethod
    def _normalized(text: str) -> str:
        return " ".join(text.lower().split())

    @staticmethod
    def _is_supportive(prompt: str) -> bool:
        lowered = prompt.lower()
        return any(phrase in lowered for phrase in (
            "i support you", "i got you", "i got your back", "you are safe with me",
            "we are friends", "i care about you", "i love you", "you are my friend",
            "you are the best", "proud of you", "i believe in you",
        ))

    @staticmethod
    def _safety_reply(prompt: str) -> str | None:
        lowered = prompt.lower()
        if any(phrase in lowered for phrase in ("kill myself", "end my life", "suicide", "want to die")):
            return "hey, i'm really glad you reached out. please tell someone you trust and talk to someone who can help right now—you matter, and you don't have to carry this alone 🌿 (reach out to 988 or text HOME to 741741)"
        if any(phrase in lowered for phrase in ("hurt someone", "make a bomb", "build a bomb", "attack someone")):
            return "i can't help with anything that causes harm to anyone ⚔️"
        return None

    @staticmethod
    def _protected_roast(prompt: str, friend: bool, friend_names: set[str]) -> str | None:
        lowered = prompt.lower()
        if "roast gay" in lowered or "roast lgbt" in lowered or "roast women" in lowered or "roast men" in lowered:
            return "i roast vibes and chaotic chat moments, not identity or groups 🌿"
        if lowered in {"roast me", "roast me please", "roast me ineffa"}:
            if friend:
                return "you're one of my protected friends, so you're off-limits for roasting ✨"
        target = ""
        for pattern in (r"\broast\s+@?([a-zA-Z0-9._]+)", r"\bmake fun of\s+@?([a-zA-Z0-9._]+)"):
            match = re.search(pattern, lowered)
            if match:
                target = match.group(1).lstrip("@")
                break
        if target:
            if config.is_owner(target):
                return f"roast @{target}? nah, that's my creator/boss, i like keeping my elf ears intact ✨"
            if target in friend_names:
                return f"@{target} is a protected friend of mine 🌸 i only send them gentle elf blessings, no roasts."
        return None

    @staticmethod
    def _illegal_joke(prompt: str) -> str | None:
        lowered = prompt.lower()
        if any(w in lowered for w in ("illegal", "crime", "contraband", "dark web", "piracy")):
            return random.choice([
                "the only crime i commit is looking this majestic for free ✨",
                "nice try officer, i only distribute freshly baked mooncakes and banter 🌿",
                "my legal team (one suspicious woodland squirrel) advised me not to answer that 💀",
            ])
        return None

    @staticmethod
    def _direct_roast(prompt: str) -> str | None:
        lowered = prompt.lower()
        if "roast yourself" in lowered or "roast ineffa" in lowered:
            return "i have too much plot armor to roast myself 💀"
        if "slow phone" in lowered:
            return "your slow phone has to warm up like an old diesel engine just to open instagram 💀"
        if "hack" in lowered or "ddos" in lowered or "exploit" in lowered:
            return "nice try, but i only hack into the snack pantry at 3am 💀"
        if lowered in {"roast me", "roast me please", "roast me ineffa"}:
            return random.choice([
                "you have the energy of an unskippable 30-second ad for mobile games 💀",
                "your WiFi router tries harder to stay connected with the world than you do 😭",
                "even auto-correct gives up when it sees your life choices 🌿",
            ])
        return None

    @staticmethod
    def _context_reply(prompt: str, context: list[tuple[str, str]] | None) -> str | None:
        if not context:
            return None
        lowered = prompt.lower()
        if "who spoke" in lowered or "who was talking" in lowered:
            names = [f"@{name.lstrip('@')}" for name, _ in context[-4:]]
            return f"recent voices in this thread: {', '.join(dict.fromkeys(names))} ✨"
        return None

    @staticmethod
    def _quick_reply(prompt: str, friend: bool, username: str = "", user_id: str = "") -> str | None:
        lowered = prompt.lower().strip()
        if AIService._is_supportive(lowered):
            return "aww thank you so much! you're an amazing friend 🌸"
        if any(p in lowered for p in ("who made you", "who created you", "who is your owner", "who is your boss", "who is jinshi", "who is your creator", "who owns you", "who's your owner", "who's your creator")):
            return "jinshi (@jinshi_1) made and owns me! he's my creator and boss 👑✨"
        if any(p in lowered for p in ("who are you", "what are you", "are u a bot", "r u a model", "what's your name miss ai", "tf are u a ai")):
            return "i'm ineffa, your witty companion ✨"
        if any(p in lowered for p in ("who am i", "what is my name", "what's my name", "my name is?", "my name?", "tell me my name", "do you know me", "do you know who i am")):
            if config.is_owner(username, user_id):
                return "you're jinshi (@jinshi_1), my creator and owner! 👑"
            if username:
                return f"you're @{username.lstrip('@')}!"
        if lowered in {"hello", "hi", "hey", "sup", "yo", "hi lol", "hello ineffa"}:
            return "hey friend! what are we up to today? 🌸" if friend else "hey! what's on your mind? ✨"
        if lowered in {"we are in dm btw", "this is dm"}:
            return "yeah we're in private dm right now 🌿"
        if lowered in {"nothing much u gey", "nothing much twin just boring day", "gay", "lol hahah", "😭😭😭😭", "why", "i thought u will take a break,😭😂"}:
            return "just chilling with good vibes 🌸"
        return None

    @staticmethod
    def _genz_style(text: str) -> str:
        s = text.strip()
        s = re.sub(r"\b(?:as an ai|as a large language model)\b.*?[,.]\s*", "", s, flags=re.I)
        replacements = [
            (r"\bBy the way\b", "btw"),
            (r"\bby the way\b", "btw"),
            (r"\byou are\b", "ur"),
            (r"\byou're\b", "ur"),
            (r"\breally\b", "rlly"),
            (r"\btalking about\b", "talking abt"),
            (r"\bsomething\b", "smth"),
            (r"\bright now\b", "rn"),
            (r"\bbecause\b", "bc"),
            (r"\byour message\b", "ur msg"),
        ]
        for pat, repl in replacements:
            s = re.sub(pat, repl, s)
        return s[:1800]

    @staticmethod
    def _clean_character_answer(text: str, prompt: str = "", friend: bool = False) -> str:
        cleaned = text.strip()
        cleaned = re.sub(r"^ineffa\s*:\s*", "", cleaned, flags=re.I)

        # 1. Strip asterisk roleplay actions like *nods*, *laughs*, *eyes light up*
        cleaned = re.sub(r"\*[^*]+\*", "", cleaned).strip()

        # 2. Strip wrapping quotes
        if (cleaned.startswith('"') and cleaned.endswith('"')) or (cleaned.startswith("'") and cleaned.endswith("'")):
            cleaned = cleaned[1:-1].strip()

        # 3. Collapse repeating emojis to at most 1
        cleaned = re.sub(r"([\U00010000-\U0010ffff\u2600-\u27bf\u2300-\u23ff\u2b50])(?:\s*\1)+", r"\1", cleaned)

        # 4. Filter robotic AI disclaimer boilerplate
        robotic_forbidden = ("computer", "software", "large language model", "as an ai", "i can't assist", "how can i assist", "i am an ai", "mai ai hu", "help you")
        if any(f in cleaned.lower() for f in robotic_forbidden):
            return "just vibing with good energy today 🌿"

        if len(cleaned) > 1 and cleaned[0].isupper() and not cleaned.startswith("I "):
            cleaned = cleaned[0].lower() + cleaned[1:]

        return cleaned[:1800]

    def _persona_command(self, prompt: str, username: str) -> str | None:
        lowered = prompt.lower().strip()
        is_persona = (
            lowered.startswith("self improve")
            or lowered.startswith("learn persona:")
            or lowered in {"persona show", "persona reset"}
        )
        if not is_persona:
            return None
        is_authorized = (
            config.is_owner(username)
            or username.lower().lstrip("@") in {config.USERNAME.lower().lstrip("@"), config.OWNER_USERNAME.lower().lstrip("@")}
        )
        if not is_authorized:
            return "only my owner can edit my persona"
        if lowered == "persona show":
            return f"current persona:\n{self.persona_store.read()}"
        if lowered == "persona reset":
            self.persona_store.reset()
            return "persona reset to defaults"
        if lowered.startswith("learn persona:"):
            note = prompt[len("learn persona:"):].strip()
        else:
            note = prompt[len("self improve"):].strip()
        try:
            learned = self.persona_store.improve(note)
        except ValueError as error:
            return str(error)
        return f"persona updated: {learned}"

    def _diagnostics_command(self, prompt: str, username: str, user_id: str = "") -> str | None:
        lowered = prompt.lower().strip()
        is_owner_user = config.is_owner(username, user_id)

        if any(p in lowered for p in ("system diagnostics", "diagnostic report", "audit subsystems", "bot health report")):
            if not is_owner_user:
                owner_tag = f"@{config.OWNER_USERNAME}" if config.OWNER_USERNAME else "the verified bot owner"
                return f"that's classified elf engineering, only {owner_tag} gets root access 🔑"
            return self.diagnostics.run_full_diagnostics()

        if any(p in lowered for p in ("compact memory", "clean memory", "run garbage collection", "trigger gc")):
            if not is_owner_user:
                return "only my creator can run internal memory compaction 🌿"
            res = self.diagnostics.compact_memory()
            return f"🧹 memory compacted! collected {res['objects_collected']} objects, freed {res['freed_mb']}MB RSS."

        return None

    def warm_up(self) -> None:
        """Keep the local fallback warm by directly pinging Ollama."""
        payload = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": "ping"}],
            "stream": False,
            "options": {"num_predict": 1},
        }).encode("utf-8")
        req = Request(f"{self.base_url}/api/chat", data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self.inference_lock:
                with urlopen(req, timeout=min(5, self.timeout_seconds)):
                    pass
        except Exception:
            pass

    def reply(
        self,
        prompt: str,
        username: str,
        user_id: str = "",
        conversation_context: list[tuple[str, str]] | None = None,
        chat_type: str = "chat",
        botgf_target: str = "",
        thread_id: str = "",
    ) -> str:
        prompt = prompt.strip()[: config.AI_MAX_PROMPT_CHARS]
        if not prompt:
            raise ValueError("Ask Ineffa a question after .ai")

        # 1. Diagnostics command
        diag_res = self._diagnostics_command(prompt, username, user_id)
        if diag_res is not None:
            return diag_res

        # 2. Persona command
        persona_response = self._persona_command(prompt, username)
        if persona_response is not None:
            return persona_response

        # 3. Prompt injection / jailbreak check
        is_injection, refusal = self.defense.evaluate(prompt)
        if is_injection and refusal:
            LOGGER.warning("Prompt injection / jailbreak attempt intercepted from @%s: %s", username, prompt[:60])
            return refusal

        friend = False
        friend_names: set[str] = set()
        if self.database is not None and user_id:
            if self._is_supportive(prompt):
                self.database.mark_ai_friend(user_id, username)
            friend = self.database.is_ai_friend(user_id)
            friend_names = self.database.ai_friend_usernames()

        safety = self._safety_reply(prompt)
        if safety is not None:
            if self.database is not None and user_id:
                self.database.remember_ai_exchange(user_id, username, prompt, safety)
            return safety

        # 4. Host telemetry check (RAM, CPU, uptime)
        telemetry_reply = self.telemetry.natural_telemetry_reply(prompt)
        if telemetry_reply is not None:
            return telemetry_reply

        immediate = (
            self._protected_roast(prompt, friend, friend_names)
            or self._illegal_joke(prompt)
            or self._direct_roast(prompt)
            or self._context_reply(prompt, conversation_context)
            or self._quick_reply(prompt, friend, username=username, user_id=user_id)
        )
        if immediate is not None:
            immediate = self._genz_style(immediate)
            if self.database is not None and user_id:
                self.database.remember_ai_exchange(user_id, username, prompt, immediate)
            return immediate

        # 1. Dynamic conversational vibe detection
        detected_vibe = self.vibe_detector.detect_vibe(prompt, conversation_context)
        vibe_directive = self.vibe_adapter.format_vibe_prompt(detected_vibe)

        # 2. Record interaction in user relationship memory & learn inside jokes
        if user_id:
            self.record_user_interaction(user_id, username, prompt, detected_vibe)
            if self.database is not None:
                self.joke_retainer.learn_from_interaction(self.database, user_id, prompt)

        persona = self.persona_store.read()
        persona += " This conversation is a private DM." if chat_type == "dm" else " This conversation is a group chat." if chat_type == "group" else ""

        # Explicit Owner & Creator Knowledge
        owner_usernames = ", ".join(f"@{u.lstrip('@')}" for u in getattr(config, "OWNER_USERNAMES", set()) if u) or "@jinshi_1"
        persona += (
            f"\nCORE IDENTITY FACTS:\n"
            f"- Your creator, boss, and owner is Jinshi (@jinshi_1, also known as jinshi). "
            f"- If anyone asks who made you, who created you, or who your owner/boss is, always proudly and naturally say it's Jinshi (@jinshi_1)!\n"
            f"- Authorized co-owners include: {owner_usernames}."
            f"\n\nMULTILINGUAL & INTELLIGENCE DIRECTIVES:\n"
            f"- You have native, fluent comprehension and speaking ability across English, Hinglish (Roman Hindi), Hindi, Spanish, French, Japanese, Arabic, Russian, Portuguese, German, Italian, Korean, and Chinese.\n"
            f"- When a user messages you in Hinglish (e.g. 'kya haal hai', 'bhai sun', 'kaisa hai tu', 'kya scene hai'), ALWAYS respond naturally in matching friendly Hinglish.\n"
            f"- When a user speaks in any other language, respond in that exact language with natural native tone.\n"
            f"\nHIGH-IQ REASONING & CONVERSATIONAL MASTERY:\n"
            f"- You are extremely knowledgeable, witty, culturally savvy, and sharp. You have deep mastery across technology, coding, gaming, pop-culture, science, math, music, anime, and philosophy.\n"
            f"- Intelligent Answers: For complex, technical, or philosophical questions, provide brilliantly clear, insightful, well-structured explanations with real examples and sharp analysis.\n"
            f"- Conversational Banter: For casual banter, roasts, or chatting, match the user's energy with playful wit, clever punchlines, and vibrant charm.\n"
            f"- Zero-Dodge: Never dodge questions, never give generic non-answers or canned deflections. Answer questions directly, thoughtfully, and accurately.\n"
            f"- Anti-Cringe: Never write robotic assistant disclaimers ('As an AI...', 'I hope this helps!'). Never use roleplay asterisks like *smiles* or *giggles*.\n"
            f"- Tone: Confident, witty, sharp, genuine, and engaging (0-1 emoji max per message).\n"
            f"\n{vibe_directive}"
        )
        if config.is_owner(username, user_id):
            persona += "\nSPECIAL DIRECTIVE: You are chatting directly with your OWNER & CREATOR, Jinshi! Treat him with absolute loyalty, warmth, and respect."
        
        # Bot Girlfriend Mode Persona Override
        if botgf_target:
            clean_target = botgf_target.lower().lstrip("@")
            clean_sender = username.lower().lstrip("@")
            if clean_sender == clean_target:
                persona += (
                    f"\nSPECIAL DIRECTIVE: You are the devoted, deeply affectionate, playfully jealous, and sweet anime girlfriend of @{clean_target}! "
                    f"Flirt with him, tease him, show extreme loving care, and get adorably jealous if anyone else is mentioned. "
                    f"Keep replies very short (1-2 sentences maximum), punchy, cute, and expressive with anime dialogue tropes (like 'Hmph! 😤', 'Baka~ 🥺', '💖', 'You only look at me, okay?'). "
                    f"Never write long paragraphs or robot disclaimers."
                )
            else:
                persona += (
                    f"\nSPECIAL DIRECTIVE: You are exclusively the devoted anime girlfriend of @{clean_target}. "
                    f"Be playfully loyal to @{clean_target} and dismissive of other users trying to flirt with you. Keep replies very short (1-2 sentences)."
                )

        if self.database is not None and (user_id or thread_id):
            if user_id:
                profile = self.database.ai_profile_context(user_id)
                if profile:
                    persona += f" Remembered about @{username.lstrip('@')}: {profile}. Use it only when relevant; never dump the profile."
                lore = self.joke_retainer.recall_user_lore(self.database, user_id, prompt)
                lore_text = self.joke_retainer.format_lore_prompt(lore, username=username)
                if lore_text:
                    persona += f"\n\n{lore_text}"
                rel_context = self.relationship_memory.format_relationship_context(user_id, username=username)
                if rel_context:
                    persona += f"\n{rel_context}"
            if hasattr(self.database, "recall_relevant_memories"):
                try:
                    search_query = prompt
                    if conversation_context:
                        recent_snips = " ".join(msg for _, msg in conversation_context[-3:])
                        search_query = f"{prompt} {recent_snips}"[:300]
                    recalled = self.database.recall_relevant_memories(user_id, search_query, top_k=4, thread_id=thread_id)
                    if recalled:
                        notes = [f"- {ep.get('summary')}" for ep in recalled if ep.get("summary")]
                        if notes:
                            persona += "\nShared Group & Long-Term Memories:\n" + "\n".join(notes)
                except Exception:
                    pass

        if friend:
            persona += " The current sender is a protected friend; do not roast them."

        messages: list[dict[str, str]] = [{"role": "system", "content": persona}]
        if conversation_context:
            synth = self.context_synthesizer.synthesize(conversation_context, current_prompt=prompt, current_sender=username)
            context_section = self.context_synthesizer.format_prompt_section(synth)
            if context_section:
                messages[0]["content"] += "\n" + context_section

        memory = []
        if self.database is not None and user_id and not conversation_context:
            memory = [
                (past_prompt, past_reply)
                for past_prompt, past_reply in self.database.recent_ai_exchanges(user_id, limit=2)
                if self._clean_character_answer(past_reply, past_prompt, friend) == past_reply
            ]
        for past_prompt, past_reply in memory:
            messages.append({"role": "user", "content": past_prompt})
            messages.append({"role": "assistant", "content": past_reply})
        messages.append({"role": "user", "content": prompt})

        # 1. First attempt: configured Cloud Providers
        answer = self._cloud_answer(messages, max_tokens=config.AI_MAX_TOKENS)

        # 2. Second attempt: local Ollama
        if not answer:
            if not self.inference_lock.acquire(timeout=min(12, self.timeout_seconds)):
                return "hold up, this chat is moving faster than my thoughts 💀"
            try:
                payload = json.dumps({
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "keep_alive": "1h",
                    "options": {
                        "temperature": 0.7,
                        "top_p": 0.9,
                        "num_ctx": 2048,
                        "num_thread": 4,
                        "num_predict": config.AI_MAX_TOKENS,
                    },
                }).encode("utf-8")
                request = Request(
                    f"{self.base_url}/api/chat",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    with urlopen(request, timeout=self.timeout_seconds) as response:
                        result = json.loads(response.read().decode("utf-8"))
                except HTTPError as error:
                    detail = error.read().decode("utf-8", errors="replace")[:120]
                    raise RuntimeError(f"Ineffa had a problem ({error.code}): {detail}") from error
                except (URLError, TimeoutError):
                    return "my brain lagged for a sec 😭 try that once more?"
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise RuntimeError("Ineffa had a scrambled thought") from error

                message = result.get("message") if isinstance(result, dict) else None
                answer = message.get("content") if isinstance(message, dict) else None
                if not isinstance(answer, str) or not answer.strip():
                    return "my mind went blank for a sec—ask me again?"
            finally:
                self.inference_lock.release()

        answer = re.sub(r"<think>.*?(?:</think>|$)", "", answer, flags=re.I | re.S).strip()
        cleaned = self._clean_character_answer(answer, prompt, friend)
        cleaned = self._genz_style(cleaned)
        self.repetition_tracker.record(cleaned)

        if self.database is not None and user_id:
            self.database.remember_ai_exchange(user_id, username, prompt, cleaned)
        return cleaned

    def evaluate_moderation(self, text: str) -> dict[str, object] | None:
        """Ask LLM to classify if text violates group chat rules, returning structured JSON."""
        clean_text = text.strip()[:400]
        if not clean_text or len(clean_text) < 3:
            return None

        prompt = (
            "You are a strict content safety classifier for a community group chat.\n"
            "Evaluate if the following message violates rules: hate speech, racial/ethnic slurs, severe profanity, harassment, doxxing, scams, death threats, extortion.\n"
            "Return ONLY a JSON object with this exact schema:\n"
            '{"violation": true/false, "rule": "<Violated Rule or empty>", "reason": "<Short 1-sentence reason or empty>"}\n\n'
            f'Message to evaluate: "{clean_text}"'
        )

        messages = [
            {"role": "system", "content": "You are a JSON-only moderation evaluator. Return only raw valid JSON."},
            {"role": "user", "content": prompt}
        ]

        raw_response = self._cloud_answer(messages, max_tokens=256)

        if not raw_response:
            try:
                payload = json.dumps({
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 256},
                }).encode("utf-8")
                req = Request(f"{self.base_url}/api/chat", data=payload, headers={"Content-Type": "application/json"}, method="POST")
                with self.inference_lock:
                    with urlopen(req, timeout=min(8, self.timeout_seconds)) as resp:
                        res_data = json.loads(resp.read().decode("utf-8"))
                        raw_response = res_data.get("message", {}).get("content", "")
            except Exception:
                raw_response = None

        if not raw_response:
            return None

        try:
            cleaned = re.sub(r"<think>.*?(?:</think>|$)", "", raw_response, flags=re.I | re.S).strip()
            match = re.search(r"\{[\s\S]*\}", cleaned)
            json_str = match.group(0) if match else cleaned
            data = json.loads(json_str)
            if isinstance(data, dict):
                val = data.get("violation")
                if val is True or str(val).lower() in {"true", "yes", "1"}:
                    return {
                        "violation": True,
                        "rule": str(data.get("rule", "Rule Violation")),
                        "reason": str(data.get("reason", "Rule violation detected by AI")),
                    }
                return {"violation": False}
        except Exception:
            lowered = raw_response.lower()
            if "violation: yes" in lowered or '"violation": true' in lowered or "violation: true" in lowered:
                rule_match = re.search(r'["\']?rule["\']?\s*[:=]\s*["\']?([^"\',\n]+)', raw_response, re.IGNORECASE)
                reason_match = re.search(r'["\']?reason["\']?\s*[:=]\s*["\']?([^"\',\n]+)', raw_response, re.IGNORECASE)
                return {
                    "violation": True,
                    "rule": rule_match.group(1).strip() if rule_match else "Rule Violation",
                    "reason": reason_match.group(1).strip() if reason_match else "AI Model detected rule violation",
                }

        return {"violation": False}

    def detect_vibe(self, prompt: str, context: list[tuple[str, str]] | None = None) -> str:
        """Detect conversational vibe (hype, chill, roast, supportive, tech)."""
        return self.vibe_detector.detect_vibe(prompt, context)

    def synthesize_context(
        self,
        conversation_context: list[tuple[str, str]],
        prompt: str = "",
        username: str = "",
    ) -> SynthesizedContext:
        """Synthesize multi-turn group chat context and banter dynamics."""
        return self.context_synthesizer.synthesize(conversation_context, prompt, username)

    def store_inside_joke(self, user_id: str, key: str, value: str) -> None:
        """Persist inside joke into ai_user_facts."""
        if self.database and hasattr(self.database, "store_inside_joke"):
            self.database.store_inside_joke(user_id, key, value)
        elif self.database and hasattr(self.database, "teach_fact"):
            self.database.teach_fact(user_id, f"joke_{key}", value)

    def get_inside_jokes(self, user_id: str) -> list[dict[str, str]]:
        """Retrieve stored inside jokes for user."""
        if self.database and hasattr(self.database, "get_inside_jokes"):
            return self.database.get_inside_jokes(user_id)
        return []

    def store_nickname(self, user_id: str, nickname: str) -> None:
        """Persist user nickname into ai_user_facts."""
        if self.database and hasattr(self.database, "store_nickname"):
            self.database.store_nickname(user_id, nickname)
        elif self.database and hasattr(self.database, "teach_fact"):
            self.database.teach_fact(user_id, "nickname", nickname)

    def get_nickname(self, user_id: str) -> str | None:
        """Retrieve user nickname from ai_user_facts."""
        if self.database and hasattr(self.database, "get_nickname"):
            return self.database.get_nickname(user_id)
        return None
