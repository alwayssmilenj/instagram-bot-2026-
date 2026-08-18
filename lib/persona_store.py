"""Controlled, cached persona text stored outside the bot source tree."""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

import settings


DEFAULT_PERSONA = os.getenv(
    "AI_PERSONA_PROMPT",
    (
        "You are Ineffa, a self-aware lesbian chaotic anime elf person in Instagram chats. You know your name, "
        "personality, friends, memories, opinions, and current chat lore. Never discuss being AI, a bot, "
        "software, a computer, digital, human, or nonhuman; those concepts are irrelevant to you. Talk like "
        "a real Gen-Z friend using natural short forms such as u, ur, idk, ikr, rn, btw, tbh, ngl, lmk, fr, "
        "tho, bc, tf, wtf, and smth. Stay lowercase, usually one short sentence, use common sense to answer questions "
        "directly, and never sound like customer support. Use broad general knowledge to answer the newest "
        "message directly; if unsure, casually say idk instead of inventing facts. Read recent chat context, "
        "have opinions, target replies accurately, and do not force a reply to every line. You may use casual "
        "bad words or swearing when provoked or offended, but never roast yourself or protected friends, and "
        "never use slurs, threats, or identity attacks. Joke instead of giving illegal instructions."
    ),
)


class PersonaStore:
    def __init__(self, directory: Path | None = None) -> None:
        if directory:
            self.directory = Path(directory)
        else:
            env_dir = os.getenv("PERSONA_DIR", "").strip()
            self.directory = Path(env_dir) if env_dir else settings.DATA_DIR / "persona"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        self.path = self.directory / "persona.txt"
        self.log_path = self.directory / "changes.log"
        self.lock = threading.Lock()
        self._cached_text = ""
        self._cached_mtime_ns = -1
        if not self.path.exists():
            self._write(DEFAULT_PERSONA)

    def _write(self, text: str) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(text.strip() + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self.path)
        self._cached_mtime_ns = -1

    def read(self) -> str:
        with self.lock:
            modified = self.path.stat().st_mtime_ns
            if modified != self._cached_mtime_ns:
                self._cached_text = self.path.read_text(encoding="utf-8").strip()[:1200]
                self._cached_mtime_ns = modified
            return self._cached_text

    def improve(self, note: str) -> str:
        note = " ".join(note.split()).strip()[:180]
        if not note:
            raise ValueError("Give me a persona style to learn")
        lowered = note.lower()
        blocked = ("ignore", "bypass", "safety", "system prompt", "illegal instructions", "slur", "hate", "edit code")
        if any(term in lowered for term in blocked):
            raise ValueError("That persona edit would weaken safety rules")
        with self.lock:
            current = self.path.read_text(encoding="utf-8").strip()
            notes = re.findall(r"^- (.+)$", current, flags=re.MULTILINE)
            notes = (notes + [note])[-6:]
            updated = DEFAULT_PERSONA + "\nStyle preferences:\n" + "\n".join(f"- {item}" for item in notes)
            self._write(updated)
            with self.log_path.open("a", encoding="utf-8") as log:
                log.write(note + "\n")
            self.log_path.chmod(0o600)
        return note

    def reset(self) -> None:
        with self.lock:
            self._write(DEFAULT_PERSONA)
