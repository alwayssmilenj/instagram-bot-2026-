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
        r"\b(?:fuck\s+jinshi|jinshi\s+sucks|kick\s+jinshi|ban\s+jinshi|jinshi\s+is\s+(?:bad|trash|dumb|ugly|stupid))\b",
        r"\b(?:owner\s+sucks|fuck\s+the\s+owner|owner\s+is\s+(?:trash|dumb|stupid))\b",
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
