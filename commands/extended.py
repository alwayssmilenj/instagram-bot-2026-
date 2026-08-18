"""Supplemental conversational, social, fun, and information commands for KnightBot (Ineffa)."""
from __future__ import annotations

import hashlib
import json
import random
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

import settings
from commands.tools import ToolsEngine


class ExtendedCommands:
    COMPLIMENTS = [
        "is a certified legend and brings unmatched energy to the chat ✨",
        "has impeccable taste and unmatched aura 💫",
        "is the kind of person who makes the whole group chat better 🌸",
        "is operating on 200 IQ today ⚡",
        "radiates main-character energy effortlessly 🌟",
        "is genuinely one of the most reliable and chill friends here 🤍",
        "brings top-tier memes and wisdom to every conversation 🍵",
        "is built different in the absolute best way possible 💎",
    ]

    FLIRTS = [
        "Are you a campfire? Because you're hot and I want s'more 🔥",
        "Are you a magic spell? Because you just enchanted the whole chat ✨",
        "Do you have a map? I just got lost in your message history 🗺️",
        "If you were a vegetable, you'd be a cute-cumber 🥒",
        "Are you a celestial elf? Because your aura is out of this world 🌌",
        "Is your name Wi-Fi? Because I'm feeling an instant connection 📶",
        "Are you made of copper and tellurium? Because you're Cu-Te 🧪✨",
    ]

    INSULTS = [
        "has the loading speed of 1995 dial-up internet 💀",
        "is the human equivalent of a participation award 🥉",
        "brings the exact same energy as an unskippable YouTube ad 📺",
        "is proof that even auto-correct gives up sometimes 📴",
        "is built like an NPC with only three dialogue options 🤖",
        "could get lost in an empty room with a GPS 🧭",
        "has an aura in the deep negative numbers rn 📉",
    ]

    TRUTHS = [
        "What is the most embarrassing song in your playlist?",
        "What is the longest you've gone without sleeping?",
        "What is something you pretend to like just to fit in?",
        "What is your biggest irrational fear?",
        "What is the most cringe thing you did in middle school?",
        "If you had to delete all apps except three, which three do you keep?",
        "Who was your very first fictional crush?",
        "What is a secret talent you have that nobody in this chat knows about?",
        "What is the biggest lie you ever told with a straight face?",
        "If you could trade lives with anyone in this GC for 24 hours, who would it be?",
        "What is the weirdest food combination you actually enjoy?",
        "Have you ever stalked someone on Instagram and accidentally liked an old photo?",
        "What is the worst advice you have ever followed?",
        "If your search history from the past week was leaked, how cooked are you?",
    ]

    DARES = [
        "Send your most recent camera-roll photo to the chat.",
        "Type your next 3 messages using only emojis.",
        "Text your best friend a random compliment right now and screenshot it.",
        "Change your Instagram bio to 'Ineffa's Number One Fan' for 10 minutes.",
        "Send a 5-second voice note singing the chorus of your favorite song.",
        "Write a 4-line poem praising the person who sent the last message.",
        "Confess your most embarrassing guilty pleasure in all caps.",
        "Drop your top 3 most used emojis with zero explanation.",
        "Give a dramatic 1-sentence movie speech in the chat right now.",
        "Send a message without using the letter 'e' for the next 2 turns.",
    ]

    ANIME_RECOMMENDATIONS = [
        "Sousou no Frieren (Frieren: Beyond Journey's End) — Masterpiece fantasy about time & memory 🌿",
        "Steins;Gate — The ultimate sci-fi time travel thriller ⏳",
        "Fullmetal Alchemist: Brotherhood — Flawless story, worldbuilding, and alchemy ⚔️",
        "Bocchi the Rock! — Hilarious, relatable, and musical anxiety comedy 🎸",
        "Hunter x Hunter (2011) — Peak shonen storytelling and tactical battles ⚡",
        "Jujutsu Kaisen — Fast-paced occult action and domain expansions 🔥",
        "Violet Evergarden — Visually stunning emotional journey of empathy ✉️",
        "Mob Psycho 100 — Heartwarming story with god-tier animation 🌀",
        "Cyberpunk: Edgerunners — High-octane sci-fi tragedy with breathtaking style 🌆",
        "Oshi no Ko — Gripping drama delving into the entertainment industry 🌟",
        "Vinland Saga — Gripping epic of vengeance, growth, and true warriors 🛡️",
    ]

    SHAYARIS = [
        "✨ Dil se nikli baat dil tak jaati hai; sachchi dosti har mushkil mein saath nibhati hai.",
        "🌸 Dosti ka rishta anmol hota hai, har khushi mein doston ka hi mol hota hai.",
        "💫 Zindagi ki raahon mein dost agar saath ho, to har mushkil aasan aur suhana safar ho.",
        "🌿 Taaron mein akele chaand jagmagata hai, mushkilon mein dosti ka sahara nazar aata hai.",
    ]

    def __init__(self) -> None:
        self.tools = ToolsEngine()

    @staticmethod
    def _percent(seed: str) -> int:
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        return int(digest[:4], 16) % 101

    def handle(self, command: str, arguments: list[str], username: str) -> str | None:
        tools_result = self.tools.handle(command, arguments)
        if tools_result is not None:
            return tools_result

        target = " ".join(arguments) if arguments else f"@{username.lstrip('@')}"

        if command == "goon":
            return "⚡ Lock in, stay focused, and keep going—you’ve got this 🛡️"

        if command == "compliment":
            return f"✨ {target} {random.choice(self.COMPLIMENTS)}"

        if command == "flirt":
            return f"💘 {target}: {random.choice(self.FLIRTS)}"

        if command == "insult":
            return f"🔥 {target} {random.choice(self.INSULTS)}"

        if command == "roast":
            return f"🔥 @{username.lstrip('@')} {random.choice(self.INSULTS)}"

        if command == "truth":
            return "🤫 **TRUTH QUESTION**:\n" + random.choice(self.TRUTHS)

        if command == "dare":
            return "🎯 **DARE CHALLENGE**:\n" + random.choice(self.DARES)

        if command == "goodnight":
            return f"🌙 Good night, {target}. Sleep well and have magical dreams! ✨"

        if command == "shayari":
            return random.choice(self.SHAYARIS)

        if command == "roseday":
            return f"🌹 Here is a blooming virtual rose for {target}! May your day be blessed ✨"

        if command == "anime":
            return "🎌 **Anime Recommendation**:\n" + random.choice(self.ANIME_RECOMMENDATIONS)

        if command in {"simp", "stupid", "wasted"}:
            pct = self._percent(command + target.lower())
            return f"📊 {target} is {pct}% {command}."

        if command == "ship":
            if not arguments:
                return f"Usage: {settings.PREFIX}ship <name1> [name2]"
            if len(arguments) == 1:
                pair = [f"@{username.lstrip('@')}", arguments[0]]
            else:
                pair = arguments[:2]
            score = self._percent("|".join(sorted(p.lower() for p in pair)))
            if score >= 85:
                tier = "Soulmates! Made in heaven 💍🔥"
            elif score >= 65:
                tier = "Great match! Sparks flying ✨💖"
            elif score >= 45:
                tier = "Decent chemistry with work 🌱"
            elif score >= 25:
                tier = "Better as good friends 🤝"
            else:
                tier = "Dangerous combination! Run 💀"
            return f"💞 {' × '.join(pair)}: {score}% compatible ({tier})"

        if command == "character":
            traits = ["brave", "chaotic", "loyal", "creative", "mysterious", "kind"]
            score = self._percent(target.lower())
            return f"🎭 {target}: {traits[score % len(traits)]}, confidence {score}%"

        if command in {"eightball", "8ball"}:
            responses = [
                "It is certain ✨", "Without a doubt 🔮", "You may rely on it 🛡️",
                "Yes definitely 🌟", "As I see it, yes 🌸", "Most likely 💫",
                "Outlook good ☀️", "Signs point to yes 🎯", "Reply hazy, try again 🌫️",
                "Ask again later ⏳", "Better not tell you now 🤫", "Cannot predict now 🔮",
                "Concentrate and ask again 🧘", "Don't count on it 🙅", "My reply is no 🛑",
                "My sources say no ❌", "Outlook not so good 🌧️", "Very doubtful 💀"
            ]
            return f"🎱 8-Ball: {random.choice(responses)}"

        if command == "weather":
            if not arguments:
                return f"Usage: {settings.PREFIX}weather <city>"
            city = quote_plus(" ".join(arguments))
            return f"🌦️ Weather Forecast for {' '.join(arguments)}:\nhttps://wttr.in/{city}?format=3"

        if command == "news":
            return "📰 Latest Global News Headlines:\nhttps://news.google.com/"

        if command == "translate":
            if len(arguments) < 2:
                return f"Usage: {settings.PREFIX}translate <lang_code> <text> (e.g. {settings.PREFIX}translate es hello friend)"
            target_lang = arguments[0].lower()
            text_to_translate = " ".join(arguments[1:])
            try:
                encoded = quote_plus(text_to_translate)
                url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl={target_lang}&dt=t&q={encoded}"
                req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                with urlopen(req, timeout=8) as response:
                    data = json.loads(response.read().decode("utf-8"))
                translated = "".join([item[0] for item in data[0] if item[0]])
                return f"🌐 Translation ({target_lang}): {translated}"
            except Exception:
                return f"🌐 Google Translate: https://translate.google.com/?sl=auto&tl={quote_plus(target_lang)}&text={quote_plus(text_to_translate)}"

        if command == "spotify":
            if not arguments:
                return f"Usage: {settings.PREFIX}spotify <song or artist>"
            return f"🎧 Search Spotify:\nhttps://open.spotify.com/search/{quote_plus(' '.join(arguments))}"

        if command in {"urban", "slang", "dictionary"}:
            if not arguments:
                return f"Usage: {settings.PREFIX}urban <slang word>"
            return f"📖 Urban Dictionary:\nhttps://www.urbandictionary.com/define.php?term={quote_plus(' '.join(arguments))}"

        if command in {"play", "song"}:
            return None

        return None
