"""Interactive multi-category trivia and quiz service with question generation and answer verification."""
from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from urllib.parse import quote_plus
from urllib.request import Request, urlopen


@dataclass
class TriviaQuestion:
    category: str
    question: str
    options: list[str]
    correct_option: str
    correct_answer: str
    explanation: str
    xp_reward: int = 25


class TriviaService:
    """Multi-category Trivia and Quiz Arena for Group Chats and DMs."""

    def __init__(self, ai_service: object | None = None) -> None:
        self.ai_service = ai_service

    QUESTIONS: list[TriviaQuestion] = [
        # Science & Tech
        TriviaQuestion(
            category="Technology",
            question="What year was the Python programming language first released by Guido van Rossum?",
            options=["1989", "1991", "1995", "2000"],
            correct_option="B",
            correct_answer="1991",
            explanation="Python was conceived in late 1989 and first released in February 1991.",
            xp_reward=30,
        ),
        TriviaQuestion(
            category="Science",
            question="What is the fastest moving planet in our solar system?",
            options=["Mercury", "Venus", "Mars", "Jupiter"],
            correct_option="A",
            correct_answer="Mercury",
            explanation="Mercury orbits the Sun at an average speed of ~47.87 km/s (107,082 mph).",
            xp_reward=25,
        ),
        TriviaQuestion(
            category="Technology",
            question="In cryptography, what does the 'RSA' algorithm stand for?",
            options=[
                "Rivest-Shamir-Adleman",
                "Recursive-Security-Algorithm",
                "Random-System-Authentication",
                "Robust-Secret-Array",
            ],
            correct_option="A",
            correct_answer="Rivest-Shamir-Adleman",
            explanation="Named after its inventors Ron Rivest, Adi Shamir, and Leonard Adleman (1977).",
            xp_reward=35,
        ),
        TriviaQuestion(
            category="Technology",
            question="What is the time complexity of searching in a balanced Binary Search Tree (AVL / Red-Black)?",
            options=["O(1)", "O(n)", "O(log n)", "O(n log n)"],
            correct_option="C",
            correct_answer="O(log n)",
            explanation="Balanced BSTs guarantee logarithmic O(log n) search, insertion, and deletion.",
            xp_reward=30,
        ),
        # Gaming & Anime
        TriviaQuestion(
            category="Gaming",
            question="In Genshin Impact, what is the official title of the Anemo Archon of Mondstadt?",
            options=["Morax", "Barbatos", "Beelzebul", "Buer"],
            correct_option="B",
            correct_answer="Barbatos",
            explanation="Barbatos (Venti) is the God of Freedom and the Anemo Archon of Mondstadt.",
            xp_reward=25,
        ),
        TriviaQuestion(
            category="Anime",
            question="In 'Attack on Titan', what is the name of the elite military branch that scouts outside the walls?",
            options=["Garrison", "Military Police", "Survey Corps (Scouts)", "Wall Cult"],
            correct_option="C",
            correct_answer="Survey Corps (Scouts)",
            explanation="The Survey Corps (Wings of Freedom) ventures outside the walls to fight Titans.",
            xp_reward=25,
        ),
        TriviaQuestion(
            category="Gaming",
            question="What is the highest competitive rank achievable in Valorant?",
            options=["Immortal", "Radiant", "Ascendant", "Diamond"],
            correct_option="B",
            correct_answer="Radiant",
            explanation="Radiant is the top 500 tier in each competitive regional leaderboard.",
            xp_reward=25,
        ),
        # History & Culture
        TriviaQuestion(
            category="History",
            question="Which ancient civilization built the city of Machu Picchu high in the Andes Mountains?",
            options=["Aztec", "Maya", "Inca", "Olmec"],
            correct_option="C",
            correct_answer="Inca",
            explanation="Machu Picchu was built in the 15th century by the Inca Empire under Emperor Pachacuti.",
            xp_reward=30,
        ),
        TriviaQuestion(
            category="General Knowledge",
            question="What is the rarest blood type in the human ABO blood group system?",
            options=["O Negative", "B Positive", "AB Negative", "A Negative"],
            correct_option="C",
            correct_answer="AB Negative",
            explanation="AB Negative is the rarest blood type, found in less than 1% of the global population.",
            xp_reward=30,
        ),
    ]

    def get_random_question(self, category: str = "") -> TriviaQuestion:
        cat_clean = category.strip().lower()
        if cat_clean:
            matches = [q for q in self.QUESTIONS if cat_clean in q.category.lower()]
            if matches:
                return random.choice(matches)
        return random.choice(self.QUESTIONS)

    def format_question(self, q: TriviaQuestion) -> str:
        letters = ["A", "B", "C", "D"]
        options_text = "\n".join(f"  {letters[i]}️⃣ {opt}" for i, opt in enumerate(q.options))
        return (
            f"🧠 **TRIVIA ARENA** [{q.category.upper()}]\n\n"
            f"❓ {q.question}\n\n"
            f"{options_text}\n\n"
            f"💡 *Reply with A, B, C, or D to answer! (+{q.xp_reward} XP)*"
        )

    def verify_answer(self, q: TriviaQuestion, answer: str) -> tuple[bool, str]:
        ans = answer.strip().upper().rstrip(".,!?")
        letters = ["A", "B", "C", "D"]
        is_correct = False

        if ans in ("A", "B", "C", "D") and ans == q.correct_option:
            is_correct = True
        elif q.correct_answer.lower() in answer.strip().lower():
            is_correct = True

        if is_correct:
            return True, f"🎉 **CORRECT!** (+{q.xp_reward} XP)\n✨ {q.explanation}"
        return False, f"❌ **INCORRECT!**\n💡 Correct answer: **{q.correct_option} ({q.correct_answer})**\nℹ️ {q.explanation}"

    def get_quote(self) -> str:
        quotes = [
            "“The only way to do great work is to love what you do.” — Steve Jobs",
            "“Code is like humor. When you have to explain it, it’s bad.” — Cory House",
            "“Simplicity is prerequisite for reliability.” — Edsger W. Dijkstra",
            "“Stay hungry, stay foolish.” — Whole Earth Catalog",
            "“Do what you can, with what you have, where you are.” — Theodore Roosevelt",
        ]
        return f"📜 {random.choice(quotes)}"

    def get_fact(self) -> str:
        facts = [
            "Honey never spoils. Archaeologists have found pots of honey in ancient Egyptian tombs that are over 3,000 years old and still edible.",
            "Octopuses have three hearts and blue blood.",
            "The first computer bug was an actual moth found trapped inside a Harvard Mark II computer in 1947.",
            "Bananas are curved because they grow towards the sun against gravity (negative geotropism).",
        ]
        return f"💡 **MIND-BLOWING FACT**: {random.choice(facts)}"

    def get_joke(self) -> str:
        jokes = [
            "Why do programmers prefer dark mode? Because light attracts bugs! 🐛",
            "There are 10 types of people in the world: those who understand binary, and those who don't. 💻",
            "A SQL query walks into a bar, walks up to two tables and asks: 'Can I join you?' 🍺",
            "Why did the developer go broke? Because he used up all his cache! 💸",
        ]
        return f"😂 {random.choice(jokes)}"

    def get_shayari(self) -> str:
        shayaris = [
            "Dosti ka rishta anokha hota hai,\nHar mushkil mein saath khada hota hai ✨",
            "Khwahishon se nahi girtay phal jholi mein,\nMehnat ki shaakh ko hilana hoga 💫",
            "Raat bhar rota raha aasmaan bhi,\nShayad usay bhi kisi ki yaad aayi thi 🌙",
        ]
        return f"✍️ {random.choice(shayaris)}"

    def get_anime(self) -> str:
        animes = [
            "🌸 **Frieren: Beyond Journey's End** — A melancholic, deeply beautiful fantasy masterpiece.",
            "⚔️ **Attack on Titan (Shingeki no Kyojin)** — Peak storytelling, intense plot twists, and philosophical war drama.",
            "🌌 **Steins;Gate** — The definitive sci-fi time-travel thriller.",
            "🗡️ **Vinland Saga** — A profound Viking epic about true redemption and what it means to be a true warrior.",
        ]
        return f"🎬 {random.choice(animes)}"

    def define_word(self, word: str) -> tuple[bool, str]:
        word_clean = word.strip().lower()
        if not word_clean:
            return False, "⚠️ Usage: `.define <word>`"
        try:
            url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{quote_plus(word_clean)}"
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list) and data:
                entry = data[0]
                phonetic = entry.get("phonetic") or ""
                meanings = entry.get("meanings", [])
                lines = [f"📖 **{word_clean.capitalize()}** {phonetic}".strip()]
                for m in meanings[:2]:
                    part = m.get("partOfSpeech", "")
                    defs = m.get("definitions", [])
                    if defs:
                        d_text = defs[0].get("definition", "")
                        example = defs[0].get("example", "")
                        lines.append(f"• *({part})* {d_text}")
                        if example:
                            lines.append(f"  ↳ *e.g.* \"{example}\"")
                return True, "\n".join(lines)
        except Exception:
            pass
        return True, f"📖 **{word_clean.capitalize()}**\nLookup on Urban Dictionary: https://www.urbandictionary.com/define.php?term={quote_plus(word_clean)}"
