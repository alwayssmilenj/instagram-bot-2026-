"""Interactive Community Engagement Service (Quotes, Facts, Trivia, Definitions)."""
from __future__ import annotations

import json
import random
import urllib.parse
import urllib.request
import logging

LOGGER = logging.getLogger("knightbot.trivia")


class TriviaService:
    """Provides quotes, scientific/historical facts, and dictionary definitions."""

    def __init__(self, ai_service: object = None) -> None:
        self.ai_service = ai_service

    def get_quote(self) -> str:
        quotes = [
            ("“The only way to do great work is to love what you do.”", "Steve Jobs"),
            ("“It does not matter how slowly you go as long as you do not stop.”", "Confucius"),
            ("“In the middle of difficulty lies opportunity.”", "Albert Einstein"),
            ("“Hard work beats talent when talent doesn't work hard.”", "Tim Notke"),
            ("“Fear is not evil. It tells you what your weakness is.”", "Gildarts Clive"),
            ("“Whatever you lose, you'll find it again. But what you throw away you'll never get back.”", "Kenshin Himura"),
            ("“If you don't take risks, you can't create a future.”", "Monkey D. Luffy"),
            ("“Success is not final, failure is not fatal: it is the courage to continue that counts.”", "Winston Churchill"),
        ]
        q, a = random.choice(quotes)
        return f"📜 {q}\n— *{a}*"

    def get_fact(self) -> str:
        facts = [
            "🧠 The human brain generates about 23 watts of electrical power when awake—enough to power a small lightbulb!",
            "🌌 There are more trees on Earth (approx. 3 trillion) than there are stars in the Milky Way galaxy (approx. 100-400 billion).",
            "⚡ A single bolt of lightning contains enough energy to toast 100,000 slices of bread.",
            "🐙 Octopuses have three hearts, nine brains, and blue blood.",
            "🍯 Honey never spoils. Archaeologists have found pots of honey in ancient Egyptian tombs that are over 3,000 years old and still perfectly edible.",
            "🪐 A day on Venus is longer than a year on Venus—it takes 243 Earth days to rotate once on its axis, but only 225 Earth days to orbit the Sun.",
            "🦈 Greenland sharks can live for over 400 years, making them the longest-living vertebrates on Earth.",
        ]
        return f"💡 **MIND-BLOWING FACT**:\n{random.choice(facts)}"

    def define_word(self, word: str) -> tuple[bool, str]:
        clean = word.strip().lower()
        if not clean:
            return False, "⚠️ Usage: `.define <word>` (e.g. `.define serendipity`)"

        try:
            url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(clean)}"
            req = urllib.request.Request(url, headers={"User-Agent": "KnightBot/2026"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            if isinstance(data, list) and data:
                entry = data[0]
                phonetic = entry.get("phonetic", "")
                phon_str = f" [{phonetic}]" if phonetic else ""
                meanings = entry.get("meanings", [])
                lines = [f"📖 **DEFINITION: {clean.upper()}**{phon_str}"]
                
                for m in meanings[:2]:
                    part = m.get("partOfSpeech", "meaning")
                    defs = m.get("definitions", [])
                    if defs:
                        d_text = defs[0].get("definition", "")
                        lines.append(f"\n*({part})* {d_text}")
                        example = defs[0].get("example")
                        if example:
                            lines.append(f"   _Example:_ \"{example}\"")
                return True, "\n".join(lines)
        except Exception:
            pass

        # Fallback to AI definition
        if self.ai_service and hasattr(self.ai_service, "reply"):
            try:
                res = self.ai_service.reply(f"Provide the dictionary definition, pronunciation, and an example sentence for the word '{clean}'. Be concise.", "user", "system")
                return True, f"📖 **DEFINITION: {clean.upper()}**\n\n{res.strip()}"
            except Exception:
                pass

        return False, f"⚠️ Could not find definition for \"{clean}\"."
