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


class TTSService:
    """Synthesize text into bounded AAC/M4A voice notes suitable for Instagram DMs with Hindi/Hinglish & ElevenLabs female support."""

    ALEXA_FILTER = "loudnorm=I=-16:TP=-1.5:LRA=11,volume=1.2"
    ANIME_FILTER = "asetrate=44100*1.28,atempo=1/1.28,highpass=f=150,equalizer=f=3500:width_type=h:width=1200:g=3,dynaudnorm=f=150:g=15,volume=2.2"

    LANG_ALIASES = {
        "english": "en", "en": "en", "spanish": "es", "hindi": "hi", "hinglish": "hinglish",
        "japanese": "ja", "french": "fr", "german": "de", "korean": "ko",
        "chinese": "zh", "arabic": "ar", "indonesian": "id", "russian": "ru",
        "portuguese": "pt", "italian": "it", "turkish": "tr", "vietnamese": "vi",
    }

    # High-Definition Natural Neural Voices for Edge-TTS
    EDGE_VOICES = {
        "hi": "hi-IN-SwaraNeural",            # Melodious Soft Hindi Female
        "hinglish": "hi-IN-SwaraNeural",      # Natural Hindi/Hinglish Female
        "en": "en-US-AnaNeural",              # Sweet, Cute Soft Female Voice
        "es": "es-ES-ElviraNeural",           # Spanish Female
        "fr": "fr-FR-DeniseNeural",           # French Female
        "de": "de-DE-KatjaNeural",            # German Female
        "ja": "ja-JP-NanamiNeural",           # Japanese Anime Female
        "ko": "ko-KR-SunHiNeural",            # Korean Female
        "ar": "ar-SA-ZariyahNeural",          # Arabic Female
        "pt": "pt-BR-FranciscaNeural",        # Portuguese Female
        "ru": "ru-RU-SvetlanaNeural",         # Russian Female
        "it": "it-IT-ElsaNeural",             # Italian Female
        "zh": "zh-CN-XiaoxiaoNeural",         # Chinese Female
    }

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

    def _synthesize_elevenlabs(self, text: str, output_path: Path, detected_lang: str, voice_id: str = "", strict: bool = False) -> bool:
        """Synthesize voice using ElevenLabs Multilingual V2 with candidate fallback."""
        api_key = getattr(config, "ELEVENLABS_API_KEY", "") or os.environ.get("ELEVENLABS_API_KEY", "")
        if not api_key:
            if strict:
                LOGGER.info("No ElevenLabs API key; falling back to Edge-TTS")
                return self._synthesize_edge_tts(text, output_path, detected_lang)
            return False

        candidate_voices = [
            voice_id or getattr(config, "ELEVENLABS_VOICE_ID", "n7534fCgBXcPEM82JQYu"),
            "cgSgspJ2msm6clMCkdW9",  # Jessica (Playful, Bright)
            "FGY2WhTYpPnrIDTdsKH5",  # Laura (Quirky, Anime)
            "EXAVITQu4vr4xnSDxMaL",  # Sarah (Studio Clear)
        ]
        candidate_voices = list(dict.fromkeys([v for v in candidate_voices if v]))
        model_id = getattr(config, "ELEVENLABS_MODEL", "eleven_multilingual_v2")

        for candidate_voice_id in candidate_voices:
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{candidate_voice_id}"
            payload = {
                "text": text,
                "model_id": model_id,
                "voice_settings": {
                    "stability": 0.50,
                    "similarity_boost": 0.80,
                    "style": 0.15,
                    "use_speaker_boost": True
                }
            }

            try:
                req = Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "xi-api-key": api_key,
                        "Content-Type": "application/json",
                        "Accept": "audio/mpeg"
                    },
                    method="POST"
                )
                with urlopen(req, timeout=10) as response:
                    if response.status == 200:
                        data = response.read()
                        if len(data) > 512:
                            output_path.write_bytes(data)
                            LOGGER.info("Synthesized ElevenLabs voice [%s] (%s bytes)", candidate_voice_id, len(data))
                            return True
            except urllib.error.HTTPError as http_err:
                LOGGER.debug("ElevenLabs candidate voice %s failed (HTTP %s)", candidate_voice_id, http_err.code)
                if http_err.code in (400, 401, 402, 404):
                    continue
            except Exception as error:
                LOGGER.debug("ElevenLabs candidate voice %s error: %s", candidate_voice_id, error)
                continue

        if strict:
            LOGGER.info("Falling back from ElevenLabs to Edge-TTS neural voice for requested voiceover")
            return self._synthesize_edge_tts(text, output_path, detected_lang)

        return False

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

        # Detect Hindi / Hinglish / English / explicit language
        detected_lang = detect_language(text, lang)
        clean_lang = self.LANG_ALIASES.get(detected_lang, detected_lang)

        work_dir = self._temp_dir()
        input_audio_path = work_dir / "speech.mp3"
        m4a_path = work_dir / "speech.m4a"

        audio_downloaded = False

        # ── Tier 1: ElevenLabs Multilingual Female Voice ──────────────────────
        audio_downloaded = self._synthesize_elevenlabs(text, input_audio_path, clean_lang, voice_id=voice_id, strict=strict_elevenlabs)

        if not audio_downloaded and strict_elevenlabs:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise RuntimeError("❌ ElevenLabs synthesis failed for requested audio.")

        # ── Tier 2: Microsoft Edge-TTS Neural Female Voice ───────────────────
        if not audio_downloaded and not strict_elevenlabs:
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
            return TTSDownload(path=m4a_path, text=text, work_dir=work_dir)
        except Exception as error:
            shutil.rmtree(work_dir, ignore_errors=True)
            LOGGER.error("TTS FFmpeg conversion failed for '%s': %s", text, error)
            raise RuntimeError(f"Voice synthesis failed: {error}") from error
