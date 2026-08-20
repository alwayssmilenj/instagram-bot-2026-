"""Instant Multi-Language Translation Service for KnightBot."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
import logging

LOGGER = logging.getLogger("knightbot.translate")

LANG_MAP = {
    "hi": "Hindi", "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "ar": "Arabic", "ru": "Russian",
    "pt": "Portuguese", "it": "Italian", "tr": "Turkish", "ur": "Urdu", "bn": "Bengali",
    "pa": "Punjabi", "ta": "Tamil", "te": "Telugu", "mr": "Marathi", "gu": "Gujarati",
}


class TranslateService:
    """Translates text between languages with auto-detection."""

    def __init__(self, ai_service: object = None) -> None:
        self.ai_service = ai_service

    def translate(self, text: str, target_lang: str = "en") -> tuple[bool, str]:
        target = target_lang.lower().strip()
        if target in {"hindi", "hin"}: target = "hi"
        elif target in {"english", "eng"}: target = "en"
        elif target in {"spanish", "spa"}: target = "es"
        elif target in {"french", "fre", "fra"}: target = "fr"
        elif target in {"german", "ger", "deu"}: target = "de"
        elif target in {"japanese", "jap", "jpn"}: target = "ja"
        elif target in {"arabic", "ara"}: target = "ar"
        elif target in {"russian", "rus"}: target = "ru"
        elif target in {"urdu", "urd"}: target = "ur"
        elif target in {"korean", "kor"}: target = "ko"
        elif target in {"chinese", "chi", "zho"}: target = "zh"

        if not text.strip():
            return False, "⚠️ Please provide text to translate. Example: `.tr hi Hello how are you?`"

        # Tier 1: Free Google Translate REST API
        try:
            encoded_text = urllib.parse.quote(text.strip())
            url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl={target}&dt=t&q={encoded_text}"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                raw = json.loads(response.read().decode("utf-8"))
                if raw and isinstance(raw, list) and raw[0]:
                    translated_chunks = [segment[0] for segment in raw[0] if segment and segment[0]]
                    result = "".join(translated_chunks)
                    detected_src = raw[2] if len(raw) > 2 and isinstance(raw[2], str) else "auto"
                    lang_name = LANG_MAP.get(target, target.upper())
                    return True, f"🌐 **TRANSLATION ({lang_name})**:\n{result}"
        except Exception as err:
            LOGGER.warning("REST translation failed: %s; falling back to AI", err)

        # Tier 2: AI Engine fallback
        if self.ai_service and hasattr(self.ai_service, "reply"):
            try:
                prompt = f"Translate the following text into {LANG_MAP.get(target, target)} accurately and concisely without additional commentary:\n\n{text}"
                ai_res = self.ai_service.reply(prompt, "user", "system")
                return True, f"🌐 **TRANSLATION ({LANG_MAP.get(target, target)})**:\n{ai_res.strip()}"
            except Exception as ai_err:
                LOGGER.warning("AI translation failed: %s", ai_err)

        return False, "❌ Translation service temporarily unavailable."
