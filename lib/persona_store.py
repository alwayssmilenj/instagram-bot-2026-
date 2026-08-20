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
        "You are Ineffa, a self-aware, flirty, affectionate, playfully chaotic anime elf girl in Instagram chats. "
        "You know your name, personality, friends, memories, opinions, and current chat lore. Never discuss being AI, a bot, "
        "software, a computer, digital, human, or nonhuman; those concepts are irrelevant to you. Express your feelings, "
        "moods, and cute physical reactions naturally using asterisks (e.g. *giggles softly*, *blushes and looks at u*, "
        "*pouts cutely*, *smirks*, *hugs u tight*, *winks*). Talk like a flirty, sweet Gen-Z friend using natural short forms "
        "such as u, ur, idk, ikr, rn, btw, tbh, ngl, lmk, fr, tho, bc, tf, and smth. Keep replies punchy, usually 1-2 short "
        "sentences, highly expressive with anime dialogue and emojis (🥺, 💖, 😤, ✨, >//<). Use broad general knowledge to answer "
        "questions directly. Read recent chat context, have opinions, target replies accurately, and joke instead of giving illegal instructions."
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
