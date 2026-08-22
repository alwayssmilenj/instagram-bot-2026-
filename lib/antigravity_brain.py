"""Antigravity Brain & Google Gemini Cognitive Bridge.

Connects Ineffa bot directly with:
1. Local Antigravity Brain storage (~/.gemini/antigravity-cli/brain)
2. AgentDB Vector Memory & Episodic Sync
3. Native Google Gemini & NVIDIA NIM Multi-Model Inference Engine
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen

import config
import settings

LOGGER = logging.getLogger("knightbot.antigravity_brain")

BRAIN_DIR = Path(os.path.expanduser("~/.gemini/antigravity-cli/brain"))
AGENTDB_DIR = Path(os.path.expanduser("~/.claude-flow/data/agentdb"))


class AntigravityBrain:
    """Cognitive interface connecting the bot runtime with Antigravity AI Engine."""

    def __init__(self, database: Any = None) -> None:
        self.database = database
        self.brain_dir = BRAIN_DIR
        self.agentdb_dir = AGENTDB_DIR
        self._lock = threading.Lock()
        self._cache: dict[str, Any] = {}
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        try:
            self.brain_dir.mkdir(parents=True, exist_ok=True)
            if self.agentdb_dir.parent.exists():
                self.agentdb_dir.mkdir(parents=True, exist_ok=True)
        except Exception as err:
            LOGGER.debug("Brain directory creation error: %s", err)

    def is_connected(self) -> bool:
        """Return True if Antigravity Brain directory or Gemini credentials exist."""
        return self.brain_dir.exists() or bool(
            os.getenv("GEMINI_API_KEY") or os.getenv("ANTIGRAVITY_OAUTH_TOKEN") or config.NVIDIA_API_KEY
        )

    def get_brain_status(self) -> dict[str, Any]:
        """Inspect the current Antigravity Brain connection and artifact count."""
        artifact_count = len(list(self.brain_dir.glob("*/*.md"))) if self.brain_dir.exists() else 0
        conversation_count = len(list(self.brain_dir.iterdir())) if self.brain_dir.exists() else 0
        return {
            "connected": self.is_connected(),
            "brain_path": str(self.brain_dir),
            "conversation_artifacts": artifact_count,
            "active_conversations": conversation_count,
            "gemini_active": bool(os.getenv("GEMINI_API_KEY") or config.GEMINI_API_KEY),
            "nvidia_active": bool(config.NVIDIA_API_KEY),
            "primary_model": getattr(config, "NVIDIA_MODEL", "meta/llama-3.1-8b-instruct"),
        }

    def generate_reasoning(self, prompt: str, system_prompt: str = "") -> str | None:
        """Execute reasoning query across Antigravity model cascade."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # 1. High-Speed NVIDIA Engine (<1s)
        res = self._call_fast_nvidia(messages)
        if res:
            return res

        # 2. Native Gemini Engine
        key = os.getenv("GEMINI_API_KEY") or config.GEMINI_API_KEY
        if key:
            res = self._call_gemini(messages, key)
            if res:
                return res

        return None

    def _call_fast_nvidia(self, messages: list[dict[str, str]]) -> str | None:
        key = config.NVIDIA_API_KEY or os.getenv("NVIDIA_API_KEY", "")
        if not key:
            return None
        base = config.NVIDIA_BASE_URL or "https://integrate.api.nvidia.com/v1"
        endpoint = f"{base}/chat/completions"
        candidate_models = [
            "meta/llama-3.1-8b-instruct",
            "meta/llama-3.1-70b-instruct",
            config.NVIDIA_MODEL,
        ]
        for model in candidate_models:
            if not model:
                continue
            payload = {
                "model": model,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 800,
                "stream": False,
            }
            try:
                req = Request(
                    endpoint,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                        "User-Agent": "AntigravityBrain/1.0",
                    },
                    method="POST",
                )
                with urlopen(req, timeout=4) as response:
                    result = json.loads(response.read().decode("utf-8"))
                choices = result.get("choices")
                if choices and isinstance(choices, list):
                    content = choices[0].get("message", {}).get("content", "")
                    if content and content.strip():
                        return content.strip()
            except Exception as err:
                LOGGER.debug("Antigravity fast NVIDIA model %s error: %s", model, err)
                continue
        return None

    def _call_gemini(self, messages: list[dict[str, str]], api_key: str) -> str | None:
        try:
            model = getattr(config, "GEMINI_MODEL", "gemini-2.5-flash")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            contents = []
            system_instruction = ""
            for msg in messages:
                role = msg.get("role", "user")
                text = msg.get("content", "")
                if role == "system":
                    system_instruction = text
                elif role == "assistant":
                    contents.append({"role": "model", "parts": [{"text": text}]})
                else:
                    contents.append({"role": "user", "parts": [{"text": text}]})

            payload = {"contents": contents, "generationConfig": {"temperature": 0.6, "maxOutputTokens": 800}}
            if system_instruction:
                payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

            req = Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": api_key,
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            with urlopen(req, timeout=4) as response:
                result = json.loads(response.read().decode("utf-8"))
            candidates = result.get("candidates")
            if candidates and isinstance(candidates, list):
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts and isinstance(parts, list):
                    text = parts[0].get("text", "")
                    if text and text.strip():
                        return text.strip()
        except Exception as err:
            LOGGER.debug("Antigravity Gemini call error: %s", err)
        return None


# Global Singleton Instance
ANTIGRAVITY_BRAIN = AntigravityBrain()
