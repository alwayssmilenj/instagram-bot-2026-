"""Text-to-speech audio synthesis service for Instagram voice messages."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

import settings

LOGGER = logging.getLogger("jinshi_mds")


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
    """Synthesize text into bounded AAC/M4A voice notes suitable for Instagram DMs."""

    ALEXA_FILTER = "highpass=f=80,equalizer=f=1000:width_type=h:width=300:g=1.5,equalizer=f=3000:width_type=h:width=500:g=1.2,dynaudnorm=f=150:g=15,volume=2.2"
    ANIME_FILTER = "asetrate=44100*1.28,atempo=1/1.28,highpass=f=150,equalizer=f=3500:width_type=h:width=1200:g=3,dynaudnorm=f=150:g=15,volume=2.2"

    LANG_ALIASES = {
        "english": "en-us", "en": "en-us", "spanish": "es", "hindi": "hi",
        "japanese": "ja", "french": "fr", "german": "de", "korean": "ko",
        "chinese": "zh", "arabic": "ar", "indonesian": "id", "russian": "ru",
        "portuguese": "pt", "italian": "it", "turkish": "tr", "vietnamese": "vi",
    }

    @staticmethod
    def _clean_tts_text(text: str) -> str:
        clean = " ".join(text.strip().split())
        clean = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", clean)
        return clean[:300]

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

    def synthesize(self, text: str, lang: str = "en", style: str = "alexa") -> TTSDownload:
        text = self._clean_tts_text(text)
        if not text:
            raise ValueError("Provide text to convert to voice")

        clean_lang = lang.strip().lower() if lang else "en"
        target_lang = self.LANG_ALIASES.get(clean_lang, clean_lang if len(clean_lang) <= 5 else "en-us")
        base_lang = target_lang.split("-")[0]
        encoded = urllib.parse.quote(text)

        raw_candidate_urls = [
            f"https://translate.google.com/translate_tts?ie=UTF-8&q={encoded}&tl={target_lang}&client=tw-ob",
            f"https://translate.googleapis.com/translate_tts?client=gtx&ie=UTF-8&tl={target_lang}&q={encoded}",
            f"https://translate.google.com/translate_tts?ie=UTF-8&q={encoded}&tl={base_lang}&client=tw-ob",
            f"https://translate.googleapis.com/translate_tts?client=gtx&ie=UTF-8&tl={base_lang}&q={encoded}",
            f"https://translate.google.com/translate_tts?ie=UTF-8&q={encoded}&tl=en-us&client=tw-ob",
            f"https://translate.googleapis.com/translate_tts?client=gtx&ie=UTF-8&tl=en-us&q={encoded}",
        ]
        candidate_urls = list(dict.fromkeys(raw_candidate_urls))

        work_dir = self._temp_dir()
        input_audio_path = work_dir / "speech.mp3"
        m4a_path = work_dir / "speech.m4a"

        audio_downloaded = False
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
                    with input_audio_path.open("wb") as target:
                        target.write(data)
                audio_downloaded = True
                break
            except Exception as candidate_error:
                LOGGER.debug("TTS candidate URL %s failed: %s", url, candidate_error)

        if not audio_downloaded:
            espeak = shutil.which("espeak-ng") or shutil.which("espeak")
            if espeak:
                for voice in (base_lang, "en-us", "en"):
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

        if not audio_downloaded:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise RuntimeError(f"Voice synthesis failed for '{text[:40]}'")

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
