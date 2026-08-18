"""Seventy bounded, high-utility, dependency-free daily commands."""
from __future__ import annotations

import ast
import base64
import codecs
import hashlib
import json
import math
import operator
import random
import re
import secrets
import statistics
import string
import uuid as uuid_module
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, unquote_plus


class UtilityCommands:
    NAMES = (
        "coin", "dice", "roll", "choose", "random", "rps", "slots", "calc", "reverse", "upper",
        "lower", "title", "length", "words", "repeat", "mock", "clap", "acronym", "palindrome", "binary",
        "unbinary", "hex", "unhex", "base64", "unbase64", "hash", "uuid", "password", "timestamp", "time",
        "date", "day", "urlencode", "urldecode", "percent", "tip", "split", "sort", "unique", "number",
        "sentences", "vowels", "consonants", "capitalize", "swapcase", "snake", "kebab", "camel", "slug", "initials",
        "rot13", "caesar", "morse", "unmorse", "jsonmin", "jsonpretty", "average", "median", "sum", "min",
        "max", "gcd", "lcm", "prime", "factorial", "temperature", "bmi", "age", "countdown", "shuffle",
    )

    OPS = {
        ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
        ast.USub: operator.neg, ast.UAdd: operator.pos,
    }

    MATH_FUNCS = {
        "sin": math.sin, "cos": math.cos, "tan": math.tan,
        "asin": math.asin, "acos": math.acos, "atan": math.atan,
        "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
        "sqrt": math.sqrt, "cbrt": math.cbrt if hasattr(math, "cbrt") else lambda x: x ** (1 / 3),
        "log": math.log, "log10": math.log10, "log2": math.log2,
        "exp": math.exp, "abs": abs, "round": round,
        "ceil": math.ceil, "floor": math.floor, "factorial": math.factorial,
        "rad": math.radians, "deg": math.degrees,
    }

    MATH_CONSTS = {
        "pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf,
    }

    MORSE = {
        **dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", (
            ".-", "-...", "-.-.", "-..", ".", "..-.", "--.", "....", "..", ".---",
            "-.-", ".-..", "--", "-.", "---", ".--.", "--.-", ".-.", "...", "-",
            "..-", "...-", ".--", "-..-", "-.--", "--.."
        ))),
        **dict(zip("0123456789", (
            "-----", ".----", "..---", "...--", "....-", ".....",
            "-....", "--...", "---..", "----."
        ))),
        ".": ".-.-.-", ",": "--..--", "?": "..--..", "'": ".----.",
        "!": "-.-.--", "/": "-..-.", "(": "-.--.", ")": "-.--.-",
        "&": ".-...", ":": "---...", ";": "-.-.-.", "=": "-...-",
        "+": ".-.-.", "-": "-....-", "_": "..--.-", "\"": ".-..-.",
        "$": "...-..-", "@": ".--.-.",
    }

    TIMEZONES: dict[str, float] = {
        "utc": 0.0, "gmt": 0.0,
        "est": -5.0, "edt": -4.0, "cst": -6.0, "cdt": -5.0,
        "mst": -7.0, "mdt": -6.0, "pst": -8.0, "pdt": -7.0,
        "akst": -9.0, "hst": -10.0,
        "bst": 1.0, "cet": 1.0, "cest": 2.0, "eet": 2.0, "eest": 3.0,
        "msk": 3.0, "gst": 4.0, "ist": 5.5, "npt": 5.75, "pkt": 5.0,
        "bst_bd": 6.0, "ict": 7.0, "wib": 7.0, "cst_cn": 8.0, "sgt": 8.0, "hkt": 8.0,
        "jst": 9.0, "kst": 9.0, "acst": 9.5, "aest": 10.0, "nzst": 12.0,
    }

    @classmethod
    def _calculate(cls, expression: str) -> int | float:
        # Pre-normalize expression (replace ^ with **)
        sanitized = expression.replace("^", "**")

        def evaluate(node):
            if isinstance(node, ast.Expression):
                return evaluate(node.body)
            if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
                return node.value
            if isinstance(node, ast.Name):
                lower_name = node.id.lower()
                if lower_name in cls.MATH_CONSTS:
                    return cls.MATH_CONSTS[lower_name]
                raise ValueError(f"Unknown constant: {node.id}")
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id.lower() in cls.MATH_FUNCS:
                    args = [evaluate(arg) for arg in node.args]
                    return cls.MATH_FUNCS[node.func.id.lower()](*args)
                raise ValueError(f"Unsupported math function: {ast.dump(node.func)}")
            if isinstance(node, ast.BinOp) and type(node.op) in cls.OPS:
                left, right = evaluate(node.left), evaluate(node.right)
                if isinstance(node.op, ast.Pow) and abs(right) > 20:
                    raise ValueError("Exponent too large (max 20)")
                result = cls.OPS[type(node.op)](left, right)
                if abs(result) > 10**18:
                    raise ValueError("Result exceeds maximum numeric range")
                return result
            if isinstance(node, ast.UnaryOp) and type(node.op) in cls.OPS:
                return cls.OPS[type(node.op)](evaluate(node.operand))
            raise ValueError("Invalid mathematical expression")

        if len(sanitized) > 120:
            raise ValueError("Expression too long (max 120 characters)")
        return evaluate(ast.parse(sanitized, mode="eval"))

    @staticmethod
    def _numbers(text: str) -> list[float]:
        values = [float(part) for part in re.split(r"[\s,]+", text) if part]
        if not values:
            raise ValueError("Provide numbers separated by spaces or commas")
        if len(values) > 100:
            raise ValueError("Maximum 100 numbers allowed")
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Numbers must be finite")
        return values

    @staticmethod
    def _words(text: str) -> list[str]:
        separated = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
        return re.findall(r"[A-Za-z0-9]+", separated)

    @staticmethod
    def _format_number(value: float) -> str:
        if math.isclose(value, int(value), abs_tol=1e-9):
            return str(int(round(value)))
        return f"{value:.10g}"

    def handle(self, command: str, args: list[str]) -> str | None:
        if command not in self.NAMES:
            return None
        text = " ".join(args).strip()
        try:
            if command == "coin":
                return random.choice(["🪙 Heads", "🪙 Tails"])

            if command == "dice":
                return f"🎲 Rolled a {random.randint(1, 6)}"

            if command == "roll":
                if not text:
                    return f"🎲 d6: {random.randint(1, 6)}"
                # Tabletop RPG dice parser (e.g., 2d20+5, d100, 3d6)
                dice_match = re.match(r"^(\d+)?d(\d+)(?:([+-])(\d+))?$", text.lower().replace(" ", ""))
                if dice_match:
                    num_dice = min(50, max(1, int(dice_match.group(1) or 1)))
                    sides = min(10000, max(2, int(dice_match.group(2))))
                    rolls = [random.randint(1, sides) for _ in range(num_dice)]
                    subtotal = sum(rolls)
                    sign = dice_match.group(3)
                    modifier = int(dice_match.group(4) or 0)
                    total = subtotal + modifier if sign == "+" else subtotal - modifier if sign == "-" else subtotal
                    rolls_display = ", ".join(map(str, rolls[:8])) + ("..." if len(rolls) > 8 else "")
                    mod_str = f" {sign} {modifier}" if sign else ""
                    return f"🎲 {num_dice}d{sides}{mod_str}: [{rolls_display}] = {total}"
                sides = min(10000, max(2, int(args[0]) if args[0].isdigit() else 6))
                return f"🎲 d{sides}: {random.randint(1, sides)}"

            if command == "choose":
                choices = [part.strip() for part in text.split("|") if part.strip()]
                if not choices and len(args) >= 2:
                    choices = args
                return f"✨ I choose: {random.choice(choices)}" if choices else "Usage: .choose pizza | sushi | tacos"

            if command == "random":
                low, high = (int(args[0]), int(args[1])) if len(args) >= 2 else (1, 100)
                return f"🔢 Random ({min(low, high)} - {max(low, high)}): {random.randint(min(low, high), max(low, high))}"

            if command == "rps":
                choices = ["rock", "paper", "scissors"]
                emojis = {"rock": "🪨 Rock", "paper": "📄 Paper", "scissors": "✂️ Scissors"}
                bot_pick = random.choice(choices)
                if not args:
                    return "Usage: .rps <rock|paper|scissors>"
                user_pick = args[0].lower().rstrip(",;.")
                if user_pick not in choices:
                    return "Pick either: rock, paper, or scissors!"
                if user_pick == bot_pick:
                    verdict = "🤝 It's a Tie!"
                elif (
                    (user_pick == "rock" and bot_pick == "scissors")
                    or (user_pick == "paper" and bot_pick == "rock")
                    or (user_pick == "scissors" and bot_pick == "paper")
                ):
                    verdict = "🎉 You Won!"
                else:
                    verdict = "💀 You Lost! Better luck next time ✨"
                return f"🎮 Rock-Paper-Scissors:\nYou: {emojis[user_pick]} • Ineffa: {emojis[bot_pick]}\n{verdict}"

            if command == "slots":
                symbols = ["🍒", "🍋", "🍇", "🔔", "⭐", "7️⃣", "💎"]
                reels = [random.choice(symbols) for _ in range(3)]
                display = f"🎰 [ {' | '.join(reels)} ]"
                if reels[0] == reels[1] == reels[2]:
                    if reels[0] == "7️⃣":
                        return f"{display}\n🔥 GRAND JACKPOT 777! 🎉🏆✨"
                    return f"{display}\n✨ TRIPLE MATCH JACKPOT! 🏆"
                if reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
                    return f"{display}\n🌟 Double match! Nice spin ✨"
                return f"{display}\nNo match! Spin again 🍀"

            if command == "calc":
                if not text:
                    return "Usage: .calc <expression> (e.g. .calc 2+2, .calc sqrt(144) + sin(pi/2))"
                res = self._calculate(text)
                return f"🧮 Result: {self._format_number(float(res)) if isinstance(res, float) else res}"

            if command == "reverse":
                return text[::-1] if text else "Usage: .reverse text"

            if command == "upper":
                return text.upper() if text else "Usage: .upper text"

            if command == "lower":
                return text.lower() if text else "Usage: .lower text"

            if command == "title":
                return text.title() if text else "Usage: .title text"

            if command == "length":
                return f"📏 Characters: {len(text)} | Without spaces: {len(text.replace(' ', ''))}"

            if command == "words":
                return f"📝 Words count: {len(text.split())}"

            if command == "repeat":
                if not args:
                    return "Usage: .repeat <count 1-5> <text>"
                count = min(5, max(1, int(args[0]) if args[0].isdigit() else 1))
                body = " ".join(args[1:])[:300]
                return " ".join([body] * count) if body else "Usage: .repeat 3 hello"

            if command == "mock":
                return "".join(char.upper() if i % 2 else char.lower() for i, char in enumerate(text)) or "Usage: .mock text"

            if command == "clap":
                return " 👏 ".join(text.split()) if text else "Usage: .clap text"

            if command == "acronym":
                return "".join(word[0].upper() for word in text.split() if word) if text else "Usage: .acronym words"

            if command == "palindrome":
                clean = "".join(c.lower() for c in text if c.isalnum())
                if not clean:
                    return "Usage: .palindrome text"
                return f"✅ '{text}' is a palindrome!" if clean == clean[::-1] else f"❌ '{text}' is not a palindrome."

            if command == "binary":
                return " ".join(format(byte, "08b") for byte in text.encode("utf-8")) if text else "Usage: .binary text"

            if command == "unbinary":
                if not text:
                    return "Usage: .unbinary <01001000 01101001>"
                clean_bits = re.findall(r"[01]{1,8}", text)
                return bytes(int(b, 2) for b in clean_bits).decode("utf-8", errors="replace")

            if command == "hex":
                return text.encode("utf-8").hex() if text else "Usage: .hex text"

            if command == "unhex":
                clean_hex = re.sub(r"[^0-9a-fA-F]", "", text)
                return bytes.fromhex(clean_hex).decode("utf-8", errors="replace") if clean_hex else "Usage: .unhex <hex_string>"

            if command == "base64":
                return base64.b64encode(text.encode("utf-8")).decode("ascii") if text else "Usage: .base64 text"

            if command == "unbase64":
                return base64.b64decode(text, validate=False).decode("utf-8", errors="replace") if text else "Usage: .unbase64 <base64>"

            if command == "hash":
                algos = {
                    "md5": hashlib.md5, "sha1": hashlib.sha1, "sha224": hashlib.sha224,
                    "sha256": hashlib.sha256, "sha384": hashlib.sha384, "sha512": hashlib.sha512,
                }
                if len(args) >= 2 and args[0].lower() in algos:
                    algo_choice = args[0].lower()
                    payload = " ".join(args[1:])
                    digest = algos[algo_choice](payload.encode("utf-8")).hexdigest()
                    return f"🔒 {algo_choice.upper()}: {digest}"
                if text:
                    return f"🔒 SHA256: {hashlib.sha256(text.encode('utf-8')).hexdigest()}"
                return "Usage: .hash [md5|sha1|sha256|sha512] <text>"

            if command == "uuid":
                return f"🆔 UUIDv4: {uuid_module.uuid4()}"

            if command == "password":
                size = min(64, max(8, int(args[0]) if args and args[0].isdigit() else 16))
                alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
                generated = "".join(secrets.choice(alphabet) for _ in range(size))
                return f"🔐 Generated Password ({size} chars):\n`{generated}`"

            if command == "timestamp":
                now_ts = int(datetime.now(tz=timezone.utc).timestamp())
                return f"⏱️ Unix Timestamp: {now_ts}"

            if command == "time":
                requested = args[0].lower() if args else "utc"
                if requested in self.TIMEZONES:
                    offset = self.TIMEZONES[requested]
                    target_dt = datetime.now(tz=timezone.utc) + timedelta(hours=offset)
                    return f"🕒 Current Time ({requested.upper()} UTC{'+' if offset >= 0 else ''}{offset:g}):\n{target_dt.strftime('%Y-%m-%d %I:%M:%S %p')}"
                return f"🕒 Current UTC Time: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %I:%M:%S %p')}\nSupported zones: {', '.join(sorted(list(self.TIMEZONES.keys())[:14]))}..."

            if command == "date":
                return f"📅 Today's Date: {datetime.now(tz=timezone.utc).strftime('%A, %B %d, %Y')} (UTC)"

            if command == "day":
                return f"📅 Day of the week: {datetime.now(tz=timezone.utc).strftime('%A')}"

            if command == "urlencode":
                return quote_plus(text) if text else "Usage: .urlencode text"

            if command == "urldecode":
                return unquote_plus(text) if text else "Usage: .urldecode encoded_text"

            if command == "percent":
                if len(args) < 2:
                    return "Usage: .percent <part> <total>"
                p, tot = float(args[0]), float(args[1])
                return f"📊 {p} of {tot} is {p / tot * 100:.2f}%"

            if command == "tip":
                if not args:
                    return "Usage: .tip <bill_amount> [tip_percent=15]"
                amt = float(args[0])
                rate = float(args[1]) if len(args) > 1 else 15.0
                tip_val = amt * rate / 100.0
                return f"💵 Bill: ${amt:.2f} • Tip ({rate:g}%): ${tip_val:.2f} • Total: ${amt + tip_val:.2f}"

            if command == "split":
                return "\n".join(text.split("|")) if "|" in text else "Usage: .split item1 | item2 | item3"

            if command == "sort":
                words = text.split()
                return " ".join(sorted(words, key=str.lower)) if words else "Usage: .sort word1 word2 word3"

            if command == "unique":
                words = text.split()
                return " ".join(dict.fromkeys(words)) if words else "Usage: .unique words words duplicates"

            if command == "number":
                maximum = min(1_000_000, max(1, int(args[0]) if args and args[0].isdigit() else 100))
                return f"🎲 Random Number: {secrets.randbelow(maximum) + 1}"

            if command == "sentences":
                return f"📄 Sentences count: {len(re.findall(r'[^.!?]+(?:[.!?]+|$)', text)) if text else 0}"

            if command == "vowels":
                return f"🔤 Vowels count: {sum(char.lower() in 'aeiou' for char in text)}"

            if command == "consonants":
                return f"🔤 Consonants count: {sum(char.isalpha() and char.lower() not in 'aeiou' for char in text)}"

            if command == "capitalize":
                return text[:1].upper() + text[1:] if text else "Usage: .capitalize text"

            if command == "swapcase":
                return text.swapcase() if text else "Usage: .swapcase text"

            if command in {"snake", "kebab", "slug", "camel", "initials"}:
                words = self._words(text)
                if not words:
                    return f"Usage: .{command} some text here"
                if command == "snake":
                    return "_".join(w.lower() for w in words)
                if command in {"kebab", "slug"}:
                    return "-".join(w.lower() for w in words)
                if command == "camel":
                    return words[0].lower() + "".join(w.title() for w in words[1:])
                return "".join(w[0].upper() for w in words)

            if command == "rot13":
                return codecs.decode(text, "rot_13") if text else "Usage: .rot13 text"

            if command == "caesar":
                if len(args) < 2:
                    return "Usage: .caesar <shift_number> <text>"
                shift = int(args[0]) % 26
                payload = " ".join(args[1:])
                return "".join(
                    chr((ord(char) - (ord("A") if char.isupper() else ord("a")) + shift) % 26 + (ord("A") if char.isupper() else ord("a")))
                    if char.isascii() and char.isalpha() else char
                    for char in payload
                )

            if command == "morse":
                if not text:
                    return "Usage: .morse <text>"
                return " / ".join(" ".join(self.MORSE.get(c, "?") for c in word.upper()) for word in text.split())

            if command == "unmorse":
                if not text:
                    return "Usage: .unmorse <morse_code>"
                inverse = {v: k for k, v in self.MORSE.items()}
                words_out = []
                for morse_word in text.split("/"):
                    tokens = [tok.strip() for tok in morse_word.split() if tok.strip()]
                    for tok in tokens:
                        if not re.match(r"^[.\-]+$", tok) or tok not in inverse:
                            raise ValueError("Invalid Morse code")
                    chars_out = "".join(inverse[token] for token in tokens)
                    words_out.append(chars_out)
                return " ".join(words_out)

            if command in {"jsonmin", "jsonpretty"}:
                if not text:
                    return f"Usage: .{command} <JSON string>"
                parsed_json = json.loads(text)
                if command == "jsonpretty":
                    return json.dumps(parsed_json, indent=2, ensure_ascii=False)[:3500]
                return json.dumps(parsed_json, separators=(",", ":"), ensure_ascii=False)[:3500]

            if command in {"average", "median", "sum", "min", "max"}:
                nums = self._numbers(text)
                if command == "average":
                    val = math.fsum(nums) / len(nums)
                elif command == "median":
                    val = statistics.median(nums)
                elif command == "sum":
                    val = math.fsum(nums)
                elif command == "min":
                    val = min(nums)
                else:
                    val = max(nums)
                return f"📊 {command.title()}: {self._format_number(val)}"

            if command in {"gcd", "lcm"}:
                nums_int = [int(v) for v in args if v.lstrip("-").isdigit()]
                if len(nums_int) < 2:
                    return f"Usage: .{command} <int1> <int2> [int3...]"
                return f"🧮 {command.upper()}: {math.gcd(*nums_int) if command == 'gcd' else math.lcm(*nums_int)}"

            if command == "prime":
                if not args or not args[0].lstrip("-").isdigit():
                    return "Usage: .prime <integer>"
                val_int = int(args[0])
                if abs(val_int) > 10_000_000_000:
                    raise ValueError("Number too large (max 10 billion)")
                is_p = val_int >= 2 and all(val_int % d != 0 for d in range(2, math.isqrt(val_int) + 1))
                return f"✅ {val_int} is a Prime number!" if is_p else f"❌ {val_int} is NOT a prime number."

            if command == "factorial":
                if not args or not args[0].isdigit():
                    return "Usage: .factorial <0-100>"
                val_f = int(args[0])
                if val_f > 100:
                    raise ValueError("Maximum integer is 100")
                return f"🧮 {val_f}! = {math.factorial(val_f)}"

            if command == "temperature":
                if len(args) != 3:
                    return "Usage: .temperature <value> <c|f|k> <c|f|k>"
                val_t, src_u, dst_u = float(args[0]), args[1].lower(), args[2].lower()
                if src_u not in {"c", "f", "k"} or dst_u not in {"c", "f", "k"}:
                    raise ValueError("Units must be c, f, or k")
                # Convert to Celsius
                celsius = val_t if src_u == "c" else (val_t - 32.0) * 5.0 / 9.0 if src_u == "f" else val_t - 273.15
                # Convert from Celsius to Target
                final_t = celsius if dst_u == "c" else (celsius * 9.0 / 5.0) + 32.0 if dst_u == "f" else celsius + 273.15
                return f"🌡️ {val_t:g}°{src_u.upper()} = {self._format_number(final_t)}°{dst_u.upper()}"

            if command == "bmi":
                if len(args) != 2:
                    return "Usage: .bmi <kg> <height-cm>"
                w_kg, h_cm = float(args[0]), float(args[1])
                if not math.isfinite(w_kg) or not math.isfinite(h_cm) or w_kg <= 0 or h_cm <= 0:
                    raise ValueError("Weight and height must be positive finite numbers")
                h_m = h_cm / 100.0
                bmi_val = w_kg / (h_m * h_m)
                category = "Underweight" if bmi_val < 18.5 else "Normal weight" if bmi_val < 25.0 else "Overweight" if bmi_val < 30.0 else "Obese"
                return f"⚖️ BMI: {bmi_val:.1f} ({category})"

            if command == "age":
                if not args:
                    return "Usage: .age YYYY-MM-DD"
                born = date_type.fromisoformat(args[0])
                today = datetime.now(tz=timezone.utc).date()
                if born > today:
                    raise ValueError("Birth date cannot be in the future")
                years = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
                days_total = (today - born).days
                return f"🎂 Age: {years} years old ({days_total:,} days lived)"

            if command == "countdown":
                if not args or not args[0].isdigit():
                    return "Usage: .countdown <1-50>"
                cd_val = min(50, max(1, int(args[0])))
                return " ".join(map(str, range(cd_val, 0, -1))) + " 🚀 Liftoff!"

            if command == "shuffle":
                words_list = text.split()
                if not words_list:
                    return "Usage: .shuffle <words to randomize>"
                random.SystemRandom().shuffle(words_list)
                return "🔀 Shuffled: " + " ".join(words_list)

        except (ValueError, ZeroDivisionError, UnicodeError, OverflowError, KeyError, SyntaxError, IndexError) as err:
            return f"❌ Error: {str(err)[:120]}"

        return "❌ Invalid command arguments"


ToolsEngine = UtilityCommands
