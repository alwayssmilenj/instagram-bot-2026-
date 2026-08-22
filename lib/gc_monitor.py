"""Knights Of Favonius Group Chat Monitor Service.
Monitors chat messages against group rules and dispatches admin alerts with visual cards.
"""
from __future__ import annotations

import datetime
import functools
import logging
import re
import tempfile
import textwrap
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

LOGGER = logging.getLogger("jinshi_mds")


@dataclass(frozen=True)
class ViolationResult:
    rule_broken: str
    reason: str
    username: str
    timestamp: str
    group_name: str
    message_snippet: str


def format_datetime_custom(dt: datetime.datetime | None = None) -> str:
    now = dt or datetime.datetime.now()
    d = now.day
    m = now.month
    yy = now.strftime("%y")
    time_str = now.strftime("%I:%M %p").lower()
    if time_str.startswith("0"):
        time_str = time_str[1:]
    return f"({d}/{m}/{yy}) at {time_str}"


@functools.lru_cache(maxsize=32)
def _get_font(size: int = 14) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    font_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if size > 14 else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "DejaVuSans.ttf",
        "arial.ttf",
    ]
    for candidate in font_candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    return ImageFont.load_default()


# Comprehensive homoglyph translation table (Cyrillic, Greek, Symbols -> ASCII)
HOMOGLYPH_MAP = {
    # Cyrillic
    "а": "a", "б": "b", "в": "b", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "p", "с": "c", "т": "t", "у": "u",
    "ф": "f", "х": "x", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya", "ѕ": "s", "і": "i", "ї": "i",
    # Greek
    "α": "a", "β": "b", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "h",
    "θ": "th", "ι": "i", "κ": "k", "λ": "l", "μ": "u", "ν": "v", "ξ": "x",
    "ο": "o", "π": "p", "ρ": "p", "σ": "s", "ς": "s", "τ": "t", "υ": "u",
    "φ": "f", "χ": "x", "ψ": "ps", "ω": "w",
    # Numbers & Leetspeak symbols (Preserving valid ASCII letters)
    "@": "a", "4": "a", "^": "a",
    "1": "i", "!": "i", "|": "i", "¡": "i",
    "3": "e", "€": "e", "£": "e", "&": "e",
    "0": "o", "ø": "o",
    "5": "s", "$": "s", "§": "s",
    "7": "t", "+": "t", "†": "t",
    "8": "b", "ß": "b",
    "9": "g", "6": "g",
}

MULTI_CHAR_MAP = {
    "/\\": "a", "/-\\": "a", "()": "o", "[]": "o", "{}": "o", "|-|": "h", "><": "x",
    "|3": "b", "|)": "b", "|>": "b", "|\\/|": "m", "/\\/\\": "m", "(\\/)": "m",
    "|\\|": "n", "/\\/": "n", "(\\)": "n", "\\/\\/": "w", "|<": "k", "ph": "f",
}


def normalize_leetspeak(text: str) -> tuple[str, str, str, str, str]:
    """Normalize unicode homoglyphs, symbol substitutions, and delimiter tricks."""
    # 1. Unicode decomposition
    decomposed = unicodedata.normalize("NFKD", text).lower()

    # 2. Multi-char symbol replacements
    for chars, repl in MULTI_CHAR_MAP.items():
        decomposed = decomposed.replace(chars, repl)

    # 3. Single-char homoglyph map (applied BEFORE dropping non-ASCII bytes)
    for char, repl in HOMOGLYPH_MAP.items():
        decomposed = decomposed.replace(char, repl)

    # 4. Safe ASCII representation
    t = decomposed.encode("ascii", "ignore").decode("ascii")

    # 5. Tokenized de-spacing (strip intra-token delimiters while preserving word spacing)
    tokens = [re.sub(r"[^a-z0-9]", "", w) for w in t.split()]
    t_despaced_words = " ".join(filter(None, tokens))

    # 6. Full collapsed strings
    t_no_punct = re.sub(r"[^a-z0-9]+", "", t)
    t_collapsed_no_punct = re.sub(r"(.)\1+", r"\1", t_no_punct)

    # 7. Emoji-stripped raw text
    emoji_stripped = re.sub(r"[^\w\s]", "", text)

    return t, t_despaced_words, t_no_punct, t_collapsed_no_punct, emoji_stripped


class GCMonitor:
    """Group Chat Monitor enforcing community rules per GC."""

    RULES_LIST = [
        "1. Respect everyone",
        "2. No unnecessary fights",
        "3. No discrimination",
        "4. No spam",
        "5. No abuse",
        "6. Keep private matters private",
        "7. Respect different opinions",
        "8. Don’t force people to participate",
    ]

    # Rule 3: Discrimination & Hate Speech
    DISCRIMINATION_PATTERNS = (
        r"\b(?:n[i!1l|*._\s-]*g[g*._\s-]*[e3a4*._\s-]*r|n[i!1l|*._\s-]*g[g*._\s-]*[a4*._\s-]|n[i!1l|*._\s-]*g+e+r|n[i!1l|*._\s-]*g+a+)\b",
        r"\b(?:ng[a4]|ng[e3]r|n[i!1]g[a4]|n[i!1]g[e3]r|nigg|nig|neggar|negga|nibba|n1gga|n1gger)\b",
        r"\b(?:f[a4@i!1]g+[o0e3]*t*s?|f[a4@i!1]g|k[i!1]k[e3]|ch[i!1]nk|sp[i!1]c|r[e3]t[a4@]rd|tr[a4@]nn?y|wetback|gook|shemale)\b",
        r"\b(?:hate all|die all|kill all|gas all|wipe out all)\s+(?:black|white|gay|muslim|christian|jewish|hindu|women|men|asians|trans|queer)\b",
    )

    # Slurs matched on collapsed tokens
    LONG_SLUR_PATTERNS = (
        r"(?:nigger|nigga|n1gga|n1gger|faggot|figgot|tranny|wetback|retard)",
    )

    # Rule 6: Private Matters & Doxxing
    PRIVATE_INFO_PATTERNS = (
        r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
        r"\b(?:(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])\.){3}(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])\b",
        r"\b(?:doxx?ed|here is (?:his|her|their) (?:address|ip|phone|number|location|real name))\b",
        r"\b(?:leaked your|leaking (?:your|ur) (?:ip|address|phone|nudes|pics|info))\b",
    )

    # Rule 5 & 2: Abuse, Threats, Severe Harassment & Fighting
    ABUSE_FIGHT_PATTERNS = (
        r"\b(?:go kill yourself|kill (?:your|ur)self|kys|oof (?:your|ur)self|oof out|end (?:your|ur)self|jump off|commit suicide|hope you die|die in a fire|go die|i hope you get (?:shot|stabbed|killed|cancer))\b",
        r"\b(?:stfu|shut (?:the (?:fuck|fck) )?up|fuck you|fck you|fuck off|fck off|f\*ck you|f u)\b",
        r"\b(?:wanna fight|fight me|pull up|square up|knock you out|beat your ass|i will leak your|i'll leak your|i will beat (?:you|ur)|i'll beat (?:you|ur)|i will murder|i'll hurt you)\b",
        r"\b(?:useless subhuman|piece of shit|dumbass|motherfucker|bastard|asshole|dickhead|bitch|whore|slut|cunt|cuntface|skank|douchebag|dipshit)\b",
    )

    # Word-bounded Bad Words & Profanity
    BAD_WORDS_PATTERNS = (
        r"\b(?:fuck|fck|fuk|bitch|btch|asshole|bastard|motherfucker|mfucker|dickhead|cunt|slut|whore|dumbass|dumbshit|dipshit|shithead|jackass|blowjob|handjob|hentai|porn|porno|rape|rapist|nude|nudes|pedophile|pedo)\b",
    )

    # Rule 8: Coercion / Extortion
    FORCE_COERCION_PATTERNS = (
        r"\b(?:you must join|force you to|or else i will|do it or else|if you don't (?:send|play|join))\b",
        r"\b(?:send (?:me )?(?:nudes|money|cash) or (?:else|i leak|i post))\b",
    )

    # Rule 4: Spam / Scam Links / Alphanumeric Flooding
    SPAM_PATTERNS = (
        r"(?:t\.me/[a-zA-Z0-9_+]+|discord\.gg/[a-zA-Z0-9]+|chat\.whatsapp\.com/[a-zA-Z0-9]+)",
        r"([a-zA-Z0-9])\1{9,}",
    )

    # Pre-compiled regexes for high-throughput sub-millisecond execution
    COMPILED_DISCRIMINATION_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in DISCRIMINATION_PATTERNS)
    COMPILED_LONG_SLUR_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in LONG_SLUR_PATTERNS)
    COMPILED_PRIVATE_INFO_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in PRIVATE_INFO_PATTERNS)
    COMPILED_ABUSE_FIGHT_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in ABUSE_FIGHT_PATTERNS)
    COMPILED_BAD_WORDS_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in BAD_WORDS_PATTERNS)
    COMPILED_FORCE_COERCION_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in FORCE_COERCION_PATTERNS)
    COMPILED_SPAM_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in SPAM_PATTERNS)

    def check_message(
        self,
        text: str,
        username: str,
        group_name: str = "Group Chat",
    ) -> ViolationResult | None:
        if not text or len(text.strip()) < 2:
            return None

        clean_text = text.strip()
        lowered = clean_text.lower()
        norm_text, norm_despaced, norm_no_punct, norm_collapsed, emoji_stripped = normalize_leetspeak(clean_text)
        emoji_lowered = emoji_stripped.lower()
        now_str = format_datetime_custom()
        snippet = clean_text[:120]

        # 1. Discrimination & Hate Speech Check
        for pattern in self.COMPILED_DISCRIMINATION_PATTERNS:
            if (
                pattern.search(lowered)
                or pattern.search(norm_text)
                or pattern.search(norm_despaced)
                or pattern.search(emoji_lowered)
            ):
                return ViolationResult(
                    rule_broken="No discrimination",
                    reason="Hate speech / identity attack / racial slur detected",
                    username=username,
                    timestamp=now_str,
                    group_name=group_name,
                    message_snippet=snippet,
                )

        for pattern in self.COMPILED_LONG_SLUR_PATTERNS:
            if (
                pattern.search(norm_no_punct)
                or pattern.search(norm_collapsed)
                or pattern.search(norm_despaced)
            ):
                return ViolationResult(
                    rule_broken="No discrimination",
                    reason="Obfuscated slur / hate speech pattern detected",
                    username=username,
                    timestamp=now_str,
                    group_name=group_name,
                    message_snippet=snippet,
                )

        # 2. Private Matters & Doxxing Check
        for pattern in self.COMPILED_PRIVATE_INFO_PATTERNS:
            if pattern.search(lowered) or pattern.search(norm_text):
                return ViolationResult(
                    rule_broken="Keep private matters private",
                    reason="Potential private data / phone / IP / dox threat detected",
                    username=username,
                    timestamp=now_str,
                    group_name=group_name,
                    message_snippet=snippet,
                )

        # 3. Severe Abuse & Threats Check
        for pattern in self.COMPILED_ABUSE_FIGHT_PATTERNS:
            if (
                pattern.search(lowered)
                or pattern.search(norm_text)
                or pattern.search(norm_despaced)
                or pattern.search(emoji_lowered)
            ):
                return ViolationResult(
                    rule_broken="No abuse & No unnecessary fights",
                    reason="Severe abuse / threat / toxic harassment detected",
                    username=username,
                    timestamp=now_str,
                    group_name=group_name,
                    message_snippet=snippet,
                )

        # 4. Profanity & Bad Words Check
        for pattern in self.COMPILED_BAD_WORDS_PATTERNS:
            if (
                pattern.search(lowered)
                or pattern.search(norm_text)
                or pattern.search(norm_despaced)
                or pattern.search(emoji_lowered)
            ):
                return ViolationResult(
                    rule_broken="No abuse",
                    reason="Profanity / prohibited bad word detected",
                    username=username,
                    timestamp=now_str,
                    group_name=group_name,
                    message_snippet=snippet,
                )

        # 5. Coercion / Extortion Check
        for pattern in self.COMPILED_FORCE_COERCION_PATTERNS:
            if pattern.search(lowered) or pattern.search(norm_text) or pattern.search(norm_despaced):
                return ViolationResult(
                    rule_broken="Don’t force people to participate",
                    reason="Coercion / extortion / forcing participation detected",
                    username=username,
                    timestamp=now_str,
                    group_name=group_name,
                    message_snippet=snippet,
                )

        # 6. Spam / Scam Links Check
        for pattern in self.COMPILED_SPAM_PATTERNS:
            if pattern.search(clean_text) or pattern.search(lowered):
                return ViolationResult(
                    rule_broken="No spam",
                    reason="Spam link / flood character pattern detected",
                    username=username,
                    timestamp=now_str,
                    group_name=group_name,
                    message_snippet=snippet,
                )

        return None

    def check_message_ai(
        self,
        text: str,
        username: str,
        group_name: str = "Group Chat",
        ai_service: object = None,
    ) -> ViolationResult | None:
        regex_result = self.check_message(text, username, group_name=group_name)
        if regex_result:
            return regex_result

        clean_text = text.strip()
        if len(clean_text) < 3 or clean_text.startswith("."):
            return None

        if ai_service is None:
            return None

        now_str = format_datetime_custom()
        snippet = clean_text[:120]

        # Primary: structured evaluation
        if hasattr(ai_service, "evaluate_moderation"):
            try:
                eval_result = ai_service.evaluate_moderation(clean_text)
                if eval_result and isinstance(eval_result, dict):
                    val = eval_result.get("violation")
                    is_viol = (val is True) or (isinstance(val, str) and val.strip().lower() in {"true", "yes", "1"})
                    if is_viol:
                        rule = str(eval_result.get("rule", "Rule Violation"))
                        reason = str(eval_result.get("reason", "AI detected rule violation"))
                        return ViolationResult(
                            rule_broken=f"{rule} (AI Classifier)",
                            reason=reason,
                            username=username,
                            timestamp=now_str,
                            group_name=group_name,
                            message_snippet=snippet,
                        )
                    return None
            except Exception as error:
                LOGGER.debug("AI evaluate_moderation failed: %s", error)

        # Fallback: prompt reply evaluation
        if hasattr(ai_service, "reply"):
            safe_text = clean_text[:300].replace('"', '\\"')
            prompt = (
                "You are the Group Chat Moderator AI.\n"
                "Analyze if this chat message breaks any community rules (respect everyone, no fighting, no hate speech/discrimination, no spam, no abuse/threats, no doxxing/private data, respect opinions, no coercion):\n\n"
                f'Message: "{safe_text}"\n\n'
                "Format your response as:\n"
                "VIOLATION: YES or NO\n"
                "RULE: <Rule Name>\n"
                "REASON: <Short explanation>"
            )
            try:
                ai_reply = ai_service.reply(prompt, "moderator_system", "system_mod")
                if ai_reply:
                    lowered_reply = ai_reply.lower()
                    is_violation = bool(
                        re.search(r"\bviolation\s*[:=]\s*(?:yes|true)\b", lowered_reply)
                        or re.search(r'"violation"\s*:\s*true\b', lowered_reply)
                    )
                    if is_violation:
                        rule_match = re.search(r"rule\s*[:=]\s*([^\n\r,}\"]+)", ai_reply, re.IGNORECASE)
                        reason_match = re.search(r"reason\s*[:=]\s*([^\n\r,}\"]+)", ai_reply, re.IGNORECASE)
                        rule = rule_match.group(1).strip() if rule_match else "Rule Violation"
                        reason = reason_match.group(1).strip() if reason_match else "AI Model detected rule violation"
                        return ViolationResult(
                            rule_broken=f"{rule} (AI Model)",
                            reason=reason,
                            username=username,
                            timestamp=now_str,
                            group_name=group_name,
                            message_snippet=snippet,
                        )
            except Exception as error:
                LOGGER.debug("AI GC Monitor fallback check failed: %s", error)

        return None

    def create_violation_screenshot(
        self,
        violation: ViolationResult,
        recent_messages: list[tuple[str, str]] | None = None,
        output_path: Path | str | None = None,
    ) -> Path:
        """Render an authentic, pixel-accurate Instagram Direct Message chat screenshot with the breaking message."""
        width, height = 750, 950
        image = Image.new("RGB", (width, height), color=(0, 0, 0))
        try:
            draw = ImageDraw.Draw(image)

            font_title = _get_font(16)
            font_body = _get_font(14)
            font_sub = _get_font(12)

            # 1. Instagram Top Navigation Bar
            draw.rectangle([(0, 0), (width, 80)], fill=(18, 18, 18))
            draw.line([(0, 80), (width, 80)], fill=(38, 38, 38), width=1)

            draw.text((25, 40), "<", fill=(255, 255, 255), anchor="mm", font=font_title)
            draw.ellipse([(55, 22), (95, 62)], fill=(45, 55, 72), outline=(75, 85, 105), width=1)
            gc_initial = (violation.group_name[:1] or "G").upper()
            draw.text((75, 42), gc_initial, fill=(255, 255, 255), anchor="mm", font=font_title)

            clean_title = violation.group_name[:28]
            draw.text((110, 32), clean_title, fill=(245, 245, 245), anchor="lm", font=font_title)
            draw.text((110, 52), "Active in chat • Community Group", fill=(142, 142, 142), anchor="lm", font=font_sub)
            draw.text((width - 30, 40), "i", fill=(245, 245, 245), anchor="mm", font=font_title)

            # 2. Date Separator Badge
            draw.text((width // 2, 115), violation.timestamp, fill=(142, 142, 142), anchor="mm", font=font_sub)

            # 3. Chat Messages Stream
            chat_y = 150
            if recent_messages:
                for u_name, u_msg in recent_messages[-2:]:
                    if u_name.lower().lstrip("@") == violation.username.lower().lstrip("@"):
                        continue
                    draw.text((30, chat_y), f"@{u_name.lstrip('@')}", fill=(160, 160, 160), font=font_sub)
                    chat_y += 18
                    bubble_w = min(width - 80, max(120, 50 + len(u_msg[:45]) * 8))
                    draw.rounded_rectangle([(30, chat_y), (30 + bubble_w, chat_y + 36)], radius=18, fill=(38, 38, 38))
                    draw.text((45, chat_y + 18), u_msg[:45], fill=(245, 245, 245), anchor="lm", font=font_body)
                    chat_y += 50

            # 4. Offender's Breaking Rule Chat Bubble
            offender_clean = violation.username.lstrip("@")
            draw.ellipse([(20, chat_y + 10), (56, chat_y + 46)], fill=(120, 35, 45), outline=(220, 53, 69), width=2)
            draw.text((38, chat_y + 28), offender_clean[:1].upper(), fill=(255, 255, 255), anchor="mm", font=font_title)
            draw.text((68, chat_y), f"@{offender_clean}", fill=(235, 80, 90), font=font_sub)
            chat_y += 16

            # Robust word wrapping
            msg_raw = violation.message_snippet
            raw_lines = msg_raw.splitlines() or [""]
            lines: list[str] = []
            for r_line in raw_lines:
                wrapped = textwrap.wrap(r_line, width=42, break_long_words=True) or [""]
                lines.extend(wrapped)
            lines = lines[:4] or [msg_raw[:42]]

            bubble_height = max(44, 24 + len(lines) * 20)
            max_line_len = max(len(l) for l in lines) if lines else 10
            bubble_width = min(width - 100, max(200, 60 + max_line_len * 9))

            draw.rounded_rectangle(
                [(68, chat_y), (68 + bubble_width, chat_y + bubble_height)],
                radius=18,
                fill=(45, 20, 25),
                outline=(220, 53, 69),
                width=2,
            )

            msg_curr_y = chat_y + 14
            for line_str in lines:
                draw.text((84, msg_curr_y), line_str, fill=(255, 255, 255), font=font_body)
                msg_curr_y += 20

            chat_y += bubble_height + 25

            # 5. Evidence Info Box Overlay (Wrapped reason, dynamic height)
            reason_lines = textwrap.wrap(violation.reason, width=58, break_long_words=True) or [violation.reason]
            box_height = 110 + len(reason_lines) * 18
            draw.rounded_rectangle([(30, chat_y), (width - 30, min(height - 85, chat_y + box_height))], radius=12, fill=(20, 22, 30), outline=(70, 80, 110), width=2)
            draw.text((45, chat_y + 16), "[VIOLATION DETECTED BY GC MONITOR]", fill=(255, 100, 110), font=font_title)
            draw.text((45, chat_y + 42), f"Rule Broken: {violation.rule_broken}", fill=(240, 200, 80), font=font_body)
            
            r_y = chat_y + 64
            draw.text((45, r_y), "Reason: ", fill=(220, 225, 235), font=font_body)
            for r_line in reason_lines[:3]:
                draw.text((115, r_y), r_line, fill=(220, 225, 235), font=font_body)
                r_y += 18

            draw.text((45, r_y + 6), "Status: Logged & dispatched to GC Admins", fill=(140, 150, 175), font=font_sub)

            # 6. Instagram Bottom Chat Input Bar
            draw.rectangle([(0, height - 70), (width, height)], fill=(18, 18, 18))
            draw.line([(0, height - 70), (width, height - 70)], fill=(38, 38, 38), width=1)
            draw.rounded_rectangle([(20, height - 55), (width - 70, height - 15)], radius=20, fill=(38, 38, 38))
            draw.text((40, height - 35), "Message...", fill=(142, 142, 142), anchor="lm", font=font_body)

            if output_path is not None:
                dest = Path(output_path)
                dest.parent.mkdir(parents=True, exist_ok=True)
                image.save(str(dest), format="PNG")
                return dest

            temp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            image.save(temp_file.name, format="PNG")
            temp_file.close()
            return Path(temp_file.name)
        finally:
            image.close()

    @staticmethod
    def cleanup_temp_card(path: Path | str | None) -> bool:
        """Safely delete a temporary violation card screenshot from disk."""
        if not path:
            return False
        try:
            p = Path(path)
            if p.exists():
                p.unlink(missing_ok=True)
                return True
        except Exception:
            pass
        return False

    def get_rules_overview(self) -> str:
        lines = [
            "🏰 COMMUNITY RULES & GUIDELINES 🛡️\n",
            "1. 🤝 Respect everyone — Treat fellow members with dignity.",
            "2. ⚔️ No unnecessary fights — Keep disagreements constructive.",
            "3. 🚫 No discrimination — Hate speech / slurs are strictly prohibited.",
            "4. 📨 No spam — Avoid repetitive messages or unsolicited promotion.",
            "5. 🛑 No abuse — Harassment, threats, and toxic attacks will not be tolerated.",
            "6. 🔒 Keep private matters private — Zero tolerance for doxxing, PII, or leak threats.",
            "7. 💬 Respect different opinions — Encourage healthy discussions.",
            "8. 🕊️ Don't force people to participate — No coercion or intimidation.",
        ]
        return "\n".join(lines)

    @staticmethod
    def format_admin_alert(violation: ViolationResult) -> str:
        return (
            "🚨 [GC MONITOR ALERT]\n\n"
            f"🏰 Group: {violation.group_name}\n"
            f"👤 User: @{violation.username.lstrip('@')}\n"
            f"⏰ Time: {violation.timestamp}\n"
            f"⚠️ Rule Broken: {violation.rule_broken}\n"
            f"🔍 Reason: {violation.reason}\n\n"
            f"💬 Message: \"{violation.message_snippet}\""
        )

    @staticmethod
    def format_gc_warning(violation: ViolationResult) -> str:
        clean_user = violation.username.lstrip("@")
        return (
            f"⚠️ [GC MONITOR WARNING]\n\n"
            f"👤 User: @{clean_user}\n"
            f"⏰ Time: {violation.timestamp}\n"
            f"⚠️ Rule Broken: {violation.rule_broken}\n"
            f"🔍 Reason: {violation.reason}\n\n"
            f"🛡️ Please respect the group chat rules and guidelines."
        )
