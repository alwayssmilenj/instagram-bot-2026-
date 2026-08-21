"""Master Bot Command Awareness & Autonomous Action Controller for KnightBot (Ineffa).

Provides full registry awareness of all bot commands, natural language intent parsing,
parameter extraction, permission gating, and direct command execution.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from lib.policy_engine import PolicyEngine, PolicyDecision, UserRole


@dataclass
class CommandSchema:
    """Metadata schema for a registered bot command."""
    name: str
    aliases: list[str]
    description: str
    usage: str
    required_role: UserRole
    category: str
    handler_name: str
    destructive: bool = False


@dataclass
class ParsedCommandIntent:
    """Structured representation of an intent parsed from natural conversation."""
    command_name: str
    args: list[str]
    target_username: str | None
    query: str
    confidence: float
    raw_prompt: str


class CommandController:
    """Manages bot command schemas, intent translation, and execution pipelines."""

    COMMANDS: dict[str, CommandSchema] = {
        "kick": CommandSchema(
            name="kick",
            aliases=["remove", "rm", "boot", "getridof"],
            description="Remove a user from the Instagram group chat",
            usage=".kick @username",
            required_role=UserRole.GC_MODERATOR,
            category="moderation",
            handler_name="handle_kick",
            destructive=True,
        ),
        "remove": CommandSchema(
            name="remove",
            aliases=["kick", "rm", "boot"],
            description="Remove a user from the Instagram group chat",
            usage=".remove @username",
            required_role=UserRole.GC_MODERATOR,
            category="moderation",
            handler_name="handle_kick",
            destructive=True,
        ),
        "ban": CommandSchema(
            name="ban",
            aliases=["blacklist", "permaban"],
            description="Ban a user and prevent rejoining",
            usage=".ban @username",
            required_role=UserRole.GC_MODERATOR,
            category="moderation",
            handler_name="handle_ban",
            destructive=True,
        ),
        "mute": CommandSchema(
            name="mute",
            aliases=["silence", "shutup", "timeout"],
            description="Mute a user for a duration",
            usage=".mute @username [minutes]",
            required_role=UserRole.GC_MODERATOR,
            category="moderation",
            handler_name="handle_mute",
            destructive=True,
        ),
        "warn": CommandSchema(
            name="warn",
            aliases=["strike"],
            description="Issue a formal warning strike to a user",
            usage=".warn @username [reason]",
            required_role=UserRole.GC_MODERATOR,
            category="moderation",
            handler_name="handle_warn",
        ),
        "promote": CommandSchema(
            name="promote",
            aliases=["makeadmin", "admin"],
            description="Promote a user to group admin",
            usage=".promote @username",
            required_role=UserRole.FULL_SOVEREIGN,
            category="admin",
            handler_name="handle_promote",
        ),
        "demote": CommandSchema(
            name="demote",
            aliases=["unadmin", "removeadmin"],
            description="Demote a group admin",
            usage=".demote @username",
            required_role=UserRole.FULL_SOVEREIGN,
            category="admin",
            handler_name="handle_demote",
        ),
        "tagall": CommandSchema(
            name="tagall",
            aliases=["everyone", "here", "all"],
            description="Mention all active participants in the group chat",
            usage=".tagall [message]",
            required_role=UserRole.GC_MODERATOR,
            category="group",
            handler_name="handle_tagall",
        ),
        "song": CommandSchema(
            name="song",
            aliases=["play", "music", "audio", "spotify"],
            description="Search and download music tracks as audio voice notes",
            usage=".song <title or artist>",
            required_role=UserRole.STANDARD_USER,
            category="media",
            handler_name="handle_song",
        ),
        "video": CommandSchema(
            name="video",
            aliases=["download", "yt", "reel", "tiktok"],
            description="Download video from YouTube, Reels, or TikTok",
            usage=".video <url or query>",
            required_role=UserRole.STANDARD_USER,
            category="media",
            handler_name="handle_video",
        ),
        "search": CommandSchema(
            name="search",
            aliases=["google", "web", "find"],
            description="Search the web and return concise synthesized summaries",
            usage=".search <query>",
            required_role=UserRole.STANDARD_USER,
            category="utility",
            handler_name="handle_search",
        ),
        "calc": CommandSchema(
            name="calc",
            aliases=["math", "calculate", "solve"],
            description="Evaluate complex mathematical and symbolic expressions",
            usage=".calc <expression>",
            required_role=UserRole.STANDARD_USER,
            category="utility",
            handler_name="handle_calc",
        ),
        "remind": CommandSchema(
            name="remind",
            aliases=["alarm", "timer", "schedule"],
            description="Set a natural language reminder",
            usage=".remind in 10m to <text>",
            required_role=UserRole.STANDARD_USER,
            category="utility",
            handler_name="handle_remind",
        ),
        "card": CommandSchema(
            name="card",
            aliases=["quote", "render", "canvas"],
            description="Generate a stylized typography card image",
            usage=".card <text>",
            required_role=UserRole.STANDARD_USER,
            category="canvas",
            handler_name="handle_card",
        ),
        "pies": CommandSchema(
            name="pies",
            aliases=["status", "health", "system", "stats", "telemetry"],
            description="Display live system host metrics, CPU, RAM, and workers",
            usage=".pies",
            required_role=UserRole.STANDARD_USER,
            category="system",
            handler_name="handle_pies",
        ),
        "speedtest": CommandSchema(
            name="speedtest",
            aliases=["speed", "bandwidth", "netcheck"],
            description="Test host network upload, download, and ping speeds",
            usage=".speedtest",
            required_role=UserRole.STANDARD_USER,
            category="system",
            handler_name="handle_speedtest",
        ),
        "clear": CommandSchema(
            name="clear",
            aliases=["purge", "clean"],
            description="Clear recent bot command outputs",
            usage=".clear [count]",
            required_role=UserRole.GC_MODERATOR,
            category="moderation",
            handler_name="handle_clear",
        ),
        "weather": CommandSchema(
            name="weather",
            aliases=["temp", "forecast", "climate"],
            description="Fetch real-time worldwide weather and forecast",
            usage=".weather <city>",
            required_role=UserRole.STANDARD_USER,
            category="utility",
            handler_name="handle_weather",
        ),
        "tr": CommandSchema(
            name="tr",
            aliases=["translate"],
            description="Translate text to 50+ languages",
            usage=".tr <lang> <text>",
            required_role=UserRole.STANDARD_USER,
            category="utility",
            handler_name="handle_translate",
        ),
        "poll": CommandSchema(
            name="poll",
            aliases=["newpoll"],
            description="Create an interactive group chat poll",
            usage='.poll "Question" "Opt1" "Opt2"',
            required_role=UserRole.STANDARD_USER,
            category="group",
            handler_name="handle_poll",
        ),
        "quote": CommandSchema(
            name="quote",
            aliases=["motivate"],
            description="Get an inspirational or anime quote",
            usage=".quote",
            required_role=UserRole.STANDARD_USER,
            category="entertainment",
            handler_name="handle_quote",
        ),
        "fact": CommandSchema(
            name="fact",
            aliases=["randomfact"],
            description="Get a random mind-blowing fact",
            usage=".fact",
            required_role=UserRole.STANDARD_USER,
            category="entertainment",
            handler_name="handle_fact",
        ),
        "define": CommandSchema(
            name="define",
            aliases=["meaning"],
            description="Lookup dictionary definition for a word",
            usage=".define <word>",
            required_role=UserRole.STANDARD_USER,
            category="utility",
            handler_name="handle_define",
        ),
        "otts": CommandSchema(
            name="otts",
            aliases=["ttsowner", "ownertts", "tts_owner"],
            description="Generate owner/admin exclusive ElevenLabs voiceover",
            usage=".otts <text>",
            required_role=UserRole.FULL_SOVEREIGN,
            category="voice",
            handler_name="handle_ttsowner",
        ),
        "botgf": CommandSchema(
            name="botgf",
            aliases=["gf", "girlfriend", "mygf"],
            description="Activate anime girlfriend auto-response and relationship mode",
            usage=".botgf [@username | off | status]",
            required_role=UserRole.GC_MODERATOR,
            category="ai",
            handler_name="handle_botgf",
        ),
        "teach": CommandSchema(
            name="teach",
            aliases=["remember", "learn"],
            description="Teach Ineffa a new fact or memory to recall later",
            usage=".teach <fact>",
            required_role=UserRole.STANDARD_USER,
            category="ai",
            handler_name="handle_teach",
        ),
    }

    INTENT_PATTERNS = [
        (r"\b(?:teach\s+ineffa|remember\s+that|learn\s+that)\s+(.+)", "teach"),
        (r"\b(?:kick|boot|remove)\s+@?([a-zA-Z0-9._]+)\b", "kick"),
        (r"\b(?:ban|blacklist)\s+@?([a-zA-Z0-9._]+)\b", "ban"),
        (r"\b(?:mute|silence|timeout)\s+@?([a-zA-Z0-9._]+)\b", "mute"),
        (r"\b(?:ttsowner|ownertts|otts)\s+(.+)", "otts"),
        (r"\b(?:be my girlfriend|be my gf|wanna be my gf|will you be my gf|date me)\b", "botgf"),
        (r"\b(?:girlfriend mode|botgf)\s*@?([a-zA-Z0-9._]*)\b", "botgf"),
        (r"\b(?:warn|strike)\s+@?([a-zA-Z0-9._]+)\b", "warn"),
        (r"\b(?:tag\s+everyone|tagall|mention\s+all|ping\s+everyone)\b", "tagall"),
        (r"\b(?:play|download\s+song|send\s+music|get\s+audio)\s+(?:for\s+)?(.+)", "song"),
        (r"\b(?:download\s+video|get\s+reel|download\s+reel|get\s+tiktok)\s+(.+)", "video"),
        (r"\b(?:search\s+web|look\s+up|google\s+for|search\s+for)\s+(.+)", "search"),
        (r"\b(?:calculate|what's\s+the\s+math\s+for|solve)\s+([0-9+\-*/^().\s]+)", "calc"),
        (r"\b(?:weather(?:\s+in|\s+for)?|what(?:'s| is) the weather in|temp(?:erature)? in)\s+([a-zA-Z\s,.-]+)", "weather"),
        (r"\b(?:translate|tr)\s+(.+)", "tr"),
        (r"\b(?:set\s+reminder|remind\s+me\s+(?:to|in|at)|remind\s+in)\s+(.+)", "remind"),
        (r"\b(?:create\s+poll|start\s+poll|new\s+poll|poll)\s+(.+)", "poll"),
        (r"\b(?:give\s+me\s+a\s+quote|tell\s+a\s+quote|quote|inspire\s+me)\b", "quote"),
        (r"\b(?:tell\s+a\s+fact|give\s+me\s+a\s+fact|interesting\s+fact|fact)\b", "fact"),
        (r"\b(?:define|meaning\s+of)\s+([a-zA-Z0-9_-]+)", "define"),
        (r"\b(?:say\s+in\s+voice|voice\s+note|speak|tts)\s+(.+)", "tts"),
        (r"\b(?:show\s+system\s+stats|bot\s+health|system\s+status|show\s+pies|show\s+ram)\b", "pies"),
        (r"\b(?:test\s+speed|check\s+bandwidth|run\s+speedtest)\b", "speedtest"),
    ]

    def __init__(self, policy_engine: PolicyEngine | None = None) -> None:
        self.policy = policy_engine or PolicyEngine()
        self._compiled_intents = [(re.compile(p, re.IGNORECASE), cmd) for p, cmd in self.INTENT_PATTERNS]

    def parse_intent(self, prompt: str) -> ParsedCommandIntent | None:
        """Parse natural language user prompt into a structured command intent."""
        text = prompt.strip()

        # Check explicit dot command first (e.g. .song starboy)
        if text.startswith("."):
            parts = text[1:].strip().split(maxsplit=1)
            cmd_name = parts[0].lower()
            rest = parts[1] if len(parts) > 1 else ""

            # Check direct name or aliases
            for name, schema in self.COMMANDS.items():
                if cmd_name == name or cmd_name in schema.aliases:
                    target_match = re.search(r"@([a-zA-Z0-9._]+)", rest)
                    target = target_match.group(1) if target_match else None
                    return ParsedCommandIntent(
                        command_name=name,
                        args=rest.split() if rest else [],
                        target_username=target,
                        query=rest,
                        confidence=1.0,
                        raw_prompt=prompt,
                    )

        # Check natural language intent patterns
        for pattern, cmd_name in self._compiled_intents:
            match = pattern.search(text)
            if match:
                captured = match.group(1) if match.groups() else ""
                target_match = re.search(r"@([a-zA-Z0-9._]+)", text)
                target = target_match.group(1) if target_match else (captured if cmd_name in ("kick", "ban", "mute", "warn") else None)
                return ParsedCommandIntent(
                    command_name=cmd_name,
                    args=[captured] if captured else [],
                    target_username=target,
                    query=captured,
                    confidence=0.85,
                    raw_prompt=prompt,
                )

        return None

    def evaluate_and_format_execution(
        self,
        intent: ParsedCommandIntent,
        actor_id: str,
        actor_username: str,
        actor_role: UserRole,
        target_role: UserRole | None = None,
    ) -> tuple[bool, str, str | None]:
        """Verify policy permissions and return execution readiness and response message."""
        decision = self.policy.evaluate_action(
            command_name=intent.command_name,
            actor_id=actor_id,
            actor_username=actor_username,
            actor_role=actor_role,
            target_username=intent.target_username,
            target_role=target_role,
        )

        self.policy.log_action(
            command_name=intent.command_name,
            actor_id=actor_id,
            actor_username=actor_username,
            decision=decision,
            target_username=intent.target_username,
        )

        if not decision.allowed:
            return False, decision.refusal_roast or f"Access Denied: {decision.reason} ⛔", None

        # Format command execution response
        if intent.command_name in ("kick", "ban", "mute", "warn"):
            return True, f"Executing `.{intent.command_name}` on @{intent.target_username} ⚔️", intent.command_name
        elif intent.command_name in ("song", "video", "search", "calc", "remind", "pies", "speedtest", "card", "tagall"):
            return True, f"Running `.{intent.command_name} {intent.query}` ⚡", intent.command_name

        return True, f"Executing `.{intent.command_name}` ✨", intent.command_name
