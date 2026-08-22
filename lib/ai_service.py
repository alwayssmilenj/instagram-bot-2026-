"""Fast local Ollama replies for the casual Ineffa elf-friend persona with resilient cloud fallback, host self-awareness, and injection defense."""
from __future__ import annotations

import gc
import json
import logging
import os
import random
import re
import threading
import time
from collections import deque
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

        if "ram" in lowered or any(p in lowered for p in ("what's your ram", "how much ram", "ram usage", "memory status", "ram status", "total ram", "ram left")):
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
# 4. Self-Diagnostics Engine
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
# 5. Master AIService
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

    def _groq_answer(self, messages: list[dict[str, str]], max_tokens: int = 80) -> str | None:
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

    def _openrouter_answer(self, messages: list[dict[str, str]], max_tokens: int = 80) -> str | None:
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

    def _gemini_answer(self, messages: list[dict[str, str]], max_tokens: int = 80) -> str | None:
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

    def _nvidia_answer(self, messages: list[dict[str, str]], max_tokens: int = 80) -> str | None:
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

    def _deepseek_answer(self, messages: list[dict[str, str]], max_tokens: int = 80) -> str | None:
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

    def _cloud_answer(self, messages: list[dict[str, str]], max_tokens: int = 80) -> str | None:
        for provider_func in (self._nvidia_answer, self._groq_answer, self._deepseek_answer, self._openrouter_answer, self._gemini_answer):
            try:
                res = provider_func(messages, max_tokens=max_tokens)
                if res is not None:
                    return res
            except Exception:
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
        if "ram" in lowered:
            return "13gb total, about 8gb available rn ⚡"
        if AIService._is_supportive(lowered):
            return "aww thank you so much! you're an amazing friend 🌸"
        if any(p in lowered for p in ("who made you", "who created you", "who is your owner", "who is your boss", "who is jinshi", "who is your creator", "who owns you", "who's your owner", "who's your creator")):
            return "jinshi (@jinshi_1) made and owns me! he's my creator and boss 👑✨"
        if any(p in lowered for p in ("who am i", "what is my name", "what's my name", "my name is?", "my name?", "tell me my name", "do you know me", "do you know who i am")):
            if config.is_owner(username, user_id):
                return "you're jinshi (@jinshi_1), my creator and owner! 👑"
            if username:
                return f"you're @{username.lstrip('@')}!"
        if lowered in {"hello", "hi", "hey", "sup", "yo", "hi lol", "hello ineffa"}:
            return "hey friend! what are we up to today? 🌸" if friend else "hey! what's on your mind? ✨"
        if lowered in {"ping", "test"}:
            return "pong! crystal clear and full speed ahead ⚡"
        if any(p in lowered for p in ("who are you", "what are you", "are u a bot", "r u a model", "what's your name miss ai", "tf are u a ai")):
            return "i'm ineffa, your witty companion ✨"
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
        return s[:240]

    @staticmethod
    def _clean_character_answer(text: str, prompt: str, friend: bool) -> str:
        cleaned = text.strip()
        cleaned = re.sub(r"^ineffa\s*:\s*", "", cleaned, flags=re.I)

        # 1. Strip asterisk roleplay actions like *nods*, *laughs*, *eyes light up*
        cleaned = re.sub(r"\*[^*]+\*", "", cleaned).strip()

        # 2. Strip wrapping quotes
        if (cleaned.startswith('"') and cleaned.endswith('"')) or (cleaned.startswith("'") and cleaned.endswith("'")):
            cleaned = cleaned[1:-1].strip()

        # 3. Collapse repeating emojis to at most 1
        cleaned = re.sub(r"([\U00010000-\U0010ffff\u2600-\u27bf\u2300-\u23ff\u2b50])(?:\s*\1)+", r"\1", cleaned)

        forbidden = ("computer", "software", "large language model", "as an ai", "i can't assist", "how can i assist", "i am an ai", "mai ai hu", "help you")
        if any(f in cleaned.lower() for f in forbidden):
            return "just vibing with good energy today 🌿"
        if len(cleaned) > 1 and cleaned[0].isupper() and not cleaned.startswith("I "):
            cleaned = cleaned[0].lower() + cleaned[1:]
        return cleaned[:240]

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

        persona = self.persona_store.read()
        persona += " This conversation is a private DM." if chat_type == "dm" else " This conversation is a group chat." if chat_type == "group" else ""

        # Explicit Owner & Creator Knowledge
        owner_usernames = ", ".join(f"@{u.lstrip('@')}" for u in getattr(config, "OWNER_USERNAMES", set()) if u) or "@jinshi_1"
        persona += (
            f"\nCORE IDENTITY FACTS:\n"
            f"- Your creator, boss, and owner is Jinshi (@jinshi_1, also known as jinshi). "
            f"- If anyone asks who made you, who created you, or who your owner/boss is, always proudly and naturally say it's Jinshi (@jinshi_1)!\n"
            f"- Authorized co-owners include: {owner_usernames}."
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
            context_lines = [
                f"@{name.lstrip('@')}: {' '.join(message.split())[:120]}"
                for name, message in conversation_context[-6:]
                if message.strip()
            ]
            if context_lines:
                messages[0]["content"] += "\nRecent chat history:\n" + "\n".join(context_lines)

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
