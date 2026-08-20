"""Text-to-speech audio synthesis service for Instagram voice messages with Hindi/Hinglish auto-detection and ElevenLabs & Edge-TTS neural female voice support."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.error
import urllib.parse
from urllib.request import Request, urlopen
from dataclasses import dataclass
from pathlib import Path

import config
import settings

LOGGER = logging.getLogger("jinshi_mds")

# Distinct Romanized Hindi (Hinglish) vocabulary markers
HINGLISH_WORDS = {
    "acha", "accha", "achha", "achhaa", "achaa",
    "kya", "kyu", "kyun", "kaise", "kaisa", "kaisi", "kaiko",
    "hai", "hain", "hoon", "hu", "hun",
    "theek", "thik", "sahi", "galat",
    "bhai", "bhaiya", "bhaii", "bro", "yaar", "yaara", "dost", "behen", "didi",
    "nahi", "nahin", "nhi", "naahi", "mat",
    "karo", "karna", "kar", "kare", "karenge", "karta", "karti", "karte",
    "raha", "rahi", "rahe", "rahega", "rahegi",
    "batao", "bata", "bolo", "bol", "suno", "sun", "dekho", "dekh",
    "mujhe", "mera", "meri", "mere", "mujhko",
    "tujhe", "tera", "teri", "tere", "tujhko",
    "tum", "tumhe", "tumhara", "tumhari", "tumhare",
    "aap", "aapko", "aapka", "aapki", "aapke",
    "hum", "humko", "humara", "humari", "humare",
    "apna", "apni", "apne",
    "chalo", "chalte", "lelo", "dedo", "dena", "lena",
    "sabko", "sabka", "logon",
    "yaha", "yahan", "waha", "wahan", "idhar", "udhar",
    "kitna", "kitni", "kitne", "itna", "itni", "itne", "bahut", "bohot", "zyada",
    "namaste", "namaskar", "pranam", "shukriya", "dhanyawad", "alvida",
    "pagal", "ladka", "ladki", "baccha", "bache",
    "khana", "peena", "paani", "chai",
    "zaroor", "zarur", "shaandar", "shandar", "badhiya", "masti",
    "samjha", "samjhe", "samajh", "pata", "socho",
    "arre", "arey", "areyy", "waah", "sunna",
    "wala", "wali", "wale", "waala",
}

ENGLISH_WORDS = {
    "the", "be", "to", "of", "and", "a", "in", "that", "have", "i", "it",
    "for", "not", "on", "with", "he", "as", "you", "do", "at", "this", "but",
    "his", "by", "from", "they", "we", "say", "her", "she", "or", "an", "will",
    "my", "one", "all", "would", "there", "their", "what", "so", "up", "out",
    "if", "about", "who", "get", "which", "go", "me", "when", "make", "can",
    "like", "time", "no", "just", "him", "know", "take", "people", "into", "year",
    "your", "good", "some", "could", "them", "see", "other", "than", "then",
    "now", "look", "only", "come", "its", "over", "think", "also", "back", "after",
    "use", "two", "how", "our", "work", "first", "well", "way", "even", "new",
    "want", "because", "any", "these", "give", "day", "most", "us", "are", "is",
    "hello", "hi", "hey", "please", "thanks", "thank", "explain", "code", "bot"
}


def detect_language(text: str, explicit_lang: str = "") -> str:
    """Detect language from text: 'hi' for Hindi (Devanagari), 'hinglish' for Romanized Hindi, or explicit/English."""
    clean_explicit = (explicit_lang or "").strip().lower()
    if clean_explicit and clean_explicit not in ("auto", "detect", "default"):
        return clean_explicit

    if not text:
        return "en"

    # 1. Devanagari Unicode check (Hindi script)
    if re.search(r"[\u0900-\u097F]", text):
        return "hi"

    # 2. Romanized Hindi (Hinglish) keyword & token analysis
    words = [re.sub(r"[^\w]", "", w).lower() for w in text.split()]
    words = [w for w in words if w]
    if not words:
        return "en"

    hinglish_count = sum(1 for w in words if w in HINGLISH_WORDS)
    english_count = sum(1 for w in words if w in ENGLISH_WORDS)

    if hinglish_count > 0 and hinglish_count >= english_count:
        return "hinglish"
    if hinglish_count >= 2:
        return "hinglish"
    if len(words) <= 3 and hinglish_count >= 1 and english_count == 0:
        return "hinglish"

    return "en"


@dataclass
class TTSDownload:
    path: Path
    text: str
    work_dir: Path | None = None

    def cleanup(self) -> None:
        try:
            if self.work_dir and self.work_dir.exists():
                shutil.rmtree(self.work_dir, ignore_errors=True)
            elif self.path.exists():
                self.path.unlink(missing_ok=True)
        except OSError:
            pass


class KokoroEngine:
    """Local, offline neural TTS using Kokoro-82M ONNX model (af_nicole, af_heart, af_bella)."""

    def __init__(self, model_path: Path | None = None, voices_path: Path | None = None) -> None:
        self.model_path = Path(model_path or getattr(config, "KOKORO_MODEL_PATH", "") or (settings.DATA_DIR / "kokoro" / "kokoro-v1.0.onnx"))
        self.voices_path = Path(voices_path or getattr(config, "KOKORO_VOICES_PATH", "") or (settings.DATA_DIR / "kokoro" / "voices-v1.0.bin"))
        self._kokoro = None
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        return self.model_path.exists() and self.voices_path.exists()

    def _load(self):
        if self._kokoro is None:
            with self._lock:
                if self._kokoro is None:
                    try:
                        from kokoro_onnx import Kokoro
                        self._kokoro = Kokoro(str(self.model_path), str(self.voices_path))
                        LOGGER.info("Kokoro-82M neural engine loaded successfully (%s)", self.model_path.name)
                    except Exception as err:
                        LOGGER.warning("Failed to initialize Kokoro-82M engine: %s", err)
                        return None
        return self._kokoro

    def synthesize(self, text: str, output_path: Path, voice: str = "af_nicole", speed: float = 1.0, lang: str = "en-us") -> bool:
        engine = self._load()
        if engine is None:
            return False
        try:
            import soundfile as sf
            clean_voice = (voice or getattr(config, "KOKORO_VOICE", "af_nicole") or "af_nicole").strip().lower()
            # Voice aliases for character tones
            if clean_voice in ("nicole", "asmr", "soft", "breathy"):
                clean_voice = "af_nicole"
            elif clean_voice in ("heart", "anime", "cute"):
                clean_voice = "af_heart"
            elif clean_voice in ("bella", "bright", "playful"):
                clean_voice = "af_bella"
            elif clean_voice in ("sarah", "studio"):
                clean_voice = "af_sarah"
            elif clean_voice in ("sky", "sweet"):
                clean_voice = "af_sky"
            elif clean_voice in ("jessica",):
                clean_voice = "af_jessica"

            lang_code = "en-us"
            if clean_voice.startswith("bf_") or clean_voice.startswith("bm_"):
                lang_code = "en-gb"
            elif clean_voice.startswith("jf_") or clean_voice.startswith("jm_"):
                lang_code = "ja"
            elif clean_voice.startswith("zf_") or clean_voice.startswith("zm_"):
                lang_code = "zh"

            samples, sample_rate = engine.create(
                text=text,
                voice=clean_voice,
                speed=speed,
                lang=lang_code,
            )
            wav_path = output_path.with_suffix(".wav")
            sf.write(str(wav_path), samples, sample_rate)
            if wav_path.exists() and wav_path.stat().st_size > 512:
                if wav_path != output_path:
                    wav_path.replace(output_path)
                LOGGER.info("Synthesized Kokoro-82M neural voice [%s] (%s bytes)", clean_voice, output_path.stat().st_size)
                return True
        except Exception as error:
            LOGGER.warning("Kokoro-82M synthesis error for voice '%s': %s", voice, error)
        return False


class TTSService:
    """Multi-tiered neural speech synthesis service with Kokoro-82M, Edge-TTS, and Google Translate fallbacks."""

    ALEXA_FILTER = "loudnorm=I=-16:TP=-1.5:LRA=11,volume=1.2"
    ANIME_FILTER = "asetrate=44100*1.28,atempo=1/1.28,highpass=f=150,equalizer=f=3500:width_type=h:width=1200:g=3,dynaudnorm=f=150:g=15,volume=2.2"

    LANG_ALIASES = {
        "hinglish": "hi",
        "hindi": "hi",
        "en": "en",
        "english": "en",
    }

    EDGE_VOICES = {
        "hi": "hi-IN-SwaraNeural",           # Hindi Female (Warm & Expressive)
        "en": "en-US-AnaNeural",             # English Female (Clear & Youthful)
        "es": "es-ES-ElviraNeural",          # Spanish Female
        "fr": "fr-FR-DeniseNeural",          # French Female
        "de": "de-DE-KatjaNeural",           # German Female
        "ja": "ja-JP-NanamiNeural",          # Japanese Female (Anime Clear)
        "ko": "ko-KR-SunHiNeural",           # Korean Female
        "ar": "ar-SA-ZariyahNeural",          # Arabic Female
        "pt": "pt-BR-FranciscaNeural",        # Portuguese Female
        "ru": "ru-RU-SvetlanaNeural",         # Russian Female
        "it": "it-IT-ElsaNeural",             # Italian Female
        "zh": "zh-CN-XiaoxiaoNeural",         # Chinese Female
    }

    def __init__(self) -> None:
        self.kokoro = KokoroEngine()
        self.cache_dir = settings.DATA_DIR / "tts-cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _clean_tts_text(text: str) -> str:
        clean = " ".join(text.strip().split())
        clean = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", clean)
        return clean[:350]

    def _temp_dir(self) -> Path:
        temp_root = settings.BASE_DIR / "temp"
        temp_root.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(dir=temp_root))

    @staticmethod
    def _ffmpeg() -> str:
        executable = shutil.which("ffmpeg")
        if executable:
            return executable
        bundled = sorted((settings.BASE_DIR / ".browsers").glob("ffmpeg-*/ffmpeg-linux"))
        if bundled:
            return str(bundled[-1])
        return "ffmpeg"

    def _synthesize_kokoro(self, text: str, output_path: Path, voice_id: str = "") -> bool:
        """Synthesize crystal-clear local neural speech using Kokoro-82M."""
        effective_voice = voice_id or getattr(config, "KOKORO_VOICE", "af_nicole") or "af_nicole"
        return self.kokoro.synthesize(text, output_path, voice=effective_voice)

    def _synthesize_edge_tts(self, text: str, output_path: Path, detected_lang: str) -> bool:
        """Synthesize pristine crystal-clear neural female voice using Microsoft Edge-TTS."""
        try:
            import edge_tts

            voice = self.EDGE_VOICES.get(detected_lang, self.EDGE_VOICES["en"])

            async def _run():
                comm = edge_tts.Communicate(text, voice)
                await comm.save(str(output_path))

            asyncio.run(_run())
            if output_path.exists() and output_path.stat().st_size > 512:
                LOGGER.info("Synthesized Edge-TTS neural female voice [%s] (%s bytes)", voice, output_path.stat().st_size)
                return True
        except Exception as error:
            LOGGER.debug("Edge-TTS failed: %s", error)

        return False

    def synthesize(self, text: str, lang: str = "auto", style: str = "alexa", voice_id: str = "", strict_elevenlabs: bool = False) -> TTSDownload:
        text = self._clean_tts_text(text)
        if not text:
            raise ValueError("Provide text to convert to voice")

        # Check persistent audio cache for instant sub-millisecond response
        cache_key = hashlib.sha256(f"{text}:{lang}:{style}:{voice_id}".encode()).hexdigest()[:24]
        cached_target = self.cache_dir / f"{cache_key}.m4a"
        if cached_target.exists() and cached_target.stat().st_size > 512:
            LOGGER.info("TTS Cache Hit for '%s' (0ms)", text[:30])
            work_dir = self._temp_dir()
            m4a_path = work_dir / "speech.m4a"
            shutil.copy(cached_target, m4a_path)
            return TTSDownload(path=m4a_path, text=text, lang=lang, style=style, work_dir=work_dir)

        # Detect Hindi / Hinglish / English / explicit language
        detected_lang = detect_language(text, lang)
        clean_lang = self.LANG_ALIASES.get(detected_lang, detected_lang)

        work_dir = self._temp_dir()
        input_audio_path = work_dir / "speech.wav"
        m4a_path = work_dir / "speech.m4a"

        audio_downloaded = False

        # ── Tier 1: Kokoro-82M High-Fidelity Local Neural Voice (af_nicole ASMR) ───
        if self.kokoro.is_available() or voice_id:
            audio_downloaded = self._synthesize_kokoro(text, input_audio_path, voice_id=voice_id)

        # ── Tier 2: Microsoft Edge-TTS Neural Female Voice ───────────────────
        if not audio_downloaded:
            input_audio_path = work_dir / "speech.mp3"
            audio_downloaded = self._synthesize_edge_tts(text, input_audio_path, clean_lang)

        # ── Tier 3: Google Translate TTS Fallback ────────────────────────────
        if not audio_downloaded:
            tl = "hi" if clean_lang in ("hi", "hinglish") else (clean_lang if len(clean_lang) <= 5 else "en")
            encoded = urllib.parse.quote(text)
            candidate_urls = [
                f"https://translate.google.com/translate_tts?ie=UTF-8&q={encoded}&tl={tl}&client=tw-ob",
                f"https://translate.googleapis.com/translate_tts?client=gtx&ie=UTF-8&tl={tl}&q={encoded}",
                f"https://translate.google.com/translate_tts?ie=UTF-8&q={encoded}&tl=en-us&client=tw-ob",
            ]
            for url in candidate_urls:
                try:
                    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                    with urlopen(req, timeout=8) as response:
                        content_type = response.headers.get("Content-Type", "").lower()
                        if "text/html" in content_type:
                            continue
                        data = response.read()
                        if len(data) < 256:
                            continue
                        input_audio_path.write_bytes(data)
                    audio_downloaded = True
                    break
                except Exception as candidate_error:
                    LOGGER.debug("Google TTS candidate URL %s failed: %s", url, candidate_error)

        # ── Tier 4: Local espeak / espeak-ng Fallback ────────────────────────
        if not audio_downloaded:
            espeak = shutil.which("espeak-ng") or shutil.which("espeak")
            if espeak:
                for voice in (clean_lang, "hi", "en-us", "en"):
                    try:
                        wav_path = work_dir / "speech.wav"
                        subprocess.run(
                            [espeak, "-v", voice, "-s", "150", "-w", str(wav_path), "--", text],
                            check=True,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=15,
                        )
                        input_audio_path = wav_path
                        audio_downloaded = True
                        break
                    except Exception as espeak_error:
                        LOGGER.debug("Local espeak fallback with voice %s failed: %s", voice, espeak_error)

        if not audio_downloaded or not input_audio_path.exists() or input_audio_path.stat().st_size <= 0:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise RuntimeError(f"Voice synthesis failed for '{text[:40]}'")

        # ── Final AAC Conversion for Instagram Compatibility ─────────────────
        try:
            ffmpeg = self._ffmpeg()
            clean_style = (style or "alexa").lower()
            filter_graph = self.ANIME_FILTER if clean_style in ("anime", "vocaloid") else self.ALEXA_FILTER
            subprocess.run(
                [
                    ffmpeg, "-y", "-nostdin", "-i", str(input_audio_path),
                    "-af", filter_graph,
                    "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "128k", "-ar", "44100", "-ac", "1",
                    str(m4a_path),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                timeout=30,
            )
            if m4a_path.exists() and m4a_path.stat().st_size > 512:
                try:
                    shutil.copy(m4a_path, cached_target)
                except Exception:
                    pass
            return TTSDownload(path=m4a_path, text=text, work_dir=work_dir)
        except Exception as error:
            shutil.rmtree(work_dir, ignore_errors=True)
            LOGGER.error("TTS FFmpeg conversion failed for '%s': %s", text, error)
            raise RuntimeError(f"Voice synthesis failed: {error}") from error
