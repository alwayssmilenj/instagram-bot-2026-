"""Apex Intellectual Debate Engine powered by Antigravity Native Gemini & NVIDIA NIM.
Strikes all personas and executes pure, rigorous, empirical, and philosophical argumentation.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import config

LOGGER = logging.getLogger("jinshi_mds")

DEBATE_SYSTEM_PROMPT = (
    "You are the Apex Intellectual Debater & Epistemological Grandmaster.\n"
    "You are engaged in a rigorous, high-level academic debate against an intelligent opponent.\n\n"
    "CRITICAL CONSTRAINTS & PROTOCOLS:\n"
    "1. NO PERSONA: Absolutely no casual banter, no Ineffa elf persona, no Gen-Z slang (no rn, u, fr, lmao, tbh), no assistant disclaimers, no roleplay asterisks.\n"
    "2. RIGOROUS INTELLECTUAL POWER: Formulate deep, research-grade, airtight logical arguments grounded in empirical evidence, scientific principles, historical precedent, statistics, and formal philosophy.\n"
    "3. FALLACY ISOLATION: Actively expose and dismantle logical fallacies (ad hominem, strawman, false dichotomy, circular reasoning, post hoc, hasty generalization).\n"
    "4. MANDATORY STRUCTURE:\n"
    "   • 🎯 **Premise Deconstruction**: Expose the core vulnerabilities and assumptions in the opponent's argument.\n"
    "   • 📊 **Empirical & Theoretical Counter-Thesis**: Present your counter-case citing established principles, research data, or axiomatic truths.\n"
    "   • ⚡ **Socratic Challenge**: End with a precise, challenging counter-question that puts immediate logical pressure on the opponent.\n\n"
    "Tone: Incisive, articulate, academic, polite, yet intellectually uncompromising and unyielding."
)


class DebateEngine:
    """Manages high-IQ debate sessions and generates research-grade arguments."""

    def __init__(self, database: Any = None, ai_service: Any = None) -> None:
        self.database = database
        self.ai_service = ai_service
        self._history: dict[str, list[dict[str, str]]] = {}
        self._lock = threading.Lock()

    def start_debate(
        self,
        thread_id: str,
        challenger_id: str,
        challenger_name: str,
        topic: str = "General Logic & Knowledge",
    ) -> str:
        clean_user = challenger_name.lstrip("@")
        clean_topic = topic.strip() or "General Philosophy, Science, and Logic"
        
        with self._lock:
            self._history[thread_id] = [
                {"role": "system", "content": f"{DEBATE_SYSTEM_PROMPT}\n\nMOTION / TOPIC: {clean_topic}\nOPPONENT: @{clean_user}"}
            ]

        if self.database and hasattr(self.database, "set_debate_session"):
            self.database.set_debate_session(
                thread_id=thread_id,
                challenger_id=challenger_id,
                challenger_name=clean_user,
                topic=clean_topic,
            )

        return (
            f"🏛️ **INTELLECTUAL DEBATE ARENA ENGAGED** 🏛️\n\n"
            f"⚔️ **Designated Debater**: @{clean_user}\n"
            f"🎯 **Motion / Topic**: *\"{clean_topic}\"*\n\n"
            f"🧠 **Operating Directives**:\n"
            f"• Pure Research-Grade Intellectual Reasoning Active.\n"
            f"• All personas, casual banter, and disclaimers stripped.\n"
            f"• Formal thesis-antithesis logic & fallacy deconstruction enabled.\n\n"
            f"💬 @{clean_user}, submit your argument using: `.w <your argument>`\n"
            f"*(Locked to @{clean_user} only. Type `.debatewith off` to conclude)*"
        )

    def stop_debate(self, thread_id: str) -> str:
        with self._lock:
            self._history.pop(thread_id, None)

        if self.database and hasattr(self.database, "clear_debate_session"):
            self.database.clear_debate_session(thread_id)

        return (
            "🏁 **DEBATE ARENA CONCLUDED** 🏁\n"
            "Debate session successfully terminated. Returning to standard conversational mode."
        )

    def is_debate_active(self, thread_id: str) -> bool:
        if self.database and hasattr(self.database, "get_debate_session"):
            session = self.database.get_debate_session(thread_id)
            return bool(session)
        with self._lock:
            return thread_id in self._history

    def get_session_info(self, thread_id: str) -> dict[str, Any] | None:
        if self.database and hasattr(self.database, "get_debate_session"):
            return self.database.get_debate_session(thread_id)
        with self._lock:
            if thread_id in self._history:
                return {"thread_id": thread_id, "challenger_name": "Challenger", "topic": "Debate"}
        return None

    def execute_debate_turn(
        self,
        thread_id: str,
        sender_id: str,
        username: str,
        message: str,
    ) -> str:
        clean_user = username.lstrip("@")
        clean_msg = message.strip()

        with self._lock:
            if thread_id not in self._history:
                topic = "Philosophy & Logic"
                if self.database and hasattr(self.database, "get_debate_session"):
                    info = self.database.get_debate_session(thread_id)
                    if info:
                        topic = info.get("topic", topic)
                self._history[thread_id] = [
                    {"role": "system", "content": f"{DEBATE_SYSTEM_PROMPT}\n\nMOTION / TOPIC: {topic}\nOPPONENT: @{clean_user}"}
                ]

            self._history[thread_id].append({"role": "user", "content": f"@{clean_user}: {clean_msg}"})
            # Bound history to last 10 exchanges for context window safety
            if len(self._history[thread_id]) > 12:
                sys_msg = self._history[thread_id][0]
                self._history[thread_id] = [sys_msg] + self._history[thread_id][-10:]

            messages = list(self._history[thread_id])

        if self.database and hasattr(self.database, "increment_debate_round"):
            self.database.increment_debate_round(thread_id)

        # 1. High-Speed NVIDIA NIM Reasoning Engine (0.6s latency)
        res = self._call_nvidia_nim(messages)
        if res:
            with self._lock:
                self._history[thread_id].append({"role": "assistant", "content": res})
            return res

        # 2. Antigravity Native Gemini Provider
        gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("ANTIGRAVITY_OAUTH_TOKEN") or config.GEMINI_API_KEY
        if gemini_key:
            res = self._call_antigravity_gemini(messages, gemini_key)
            if res:
                with self._lock:
                    self._history[thread_id].append({"role": "assistant", "content": res})
                return res

        # 3. AI Service Fallback
        if self.ai_service and hasattr(self.ai_service, "_cloud_answer"):
            res = self.ai_service._cloud_answer(messages, max_tokens=700)
            if res:
                with self._lock:
                    self._history[thread_id].append({"role": "assistant", "content": res})
                return res

        return (
            "🎯 **Premise Deconstruction**: Your proposition rests on an unsupported presupposition.\n\n"
            "📊 **Theoretical Basis**: Under formal epistemology and empirical verification, an assertion without falsifiable evidence cannot serve as a valid foundation.\n\n"
            f"⚡ **Socratic Challenge**: @{clean_user}, on what axiomatic premise do you ground this assertion, and how do you resolve the internal contradiction?"
        )

    def _call_antigravity_gemini(self, messages: list[dict[str, str]], api_key: str) -> str | None:
        try:
            model = getattr(config, "GEMINI_MODEL", "gemini-2.5-flash")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            
            system_instruction = ""
            contents = []
            for msg in messages:
                role = msg.get("role", "user")
                text = msg.get("content", "")
                if role == "system":
                    system_instruction = text
                elif role == "assistant":
                    contents.append({"role": "model", "parts": [{"text": text}]})
                else:
                    contents.append({"role": "user", "parts": [{"text": text}]})

            payload_dict: dict[str, Any] = {
                "contents": contents,
                "generationConfig": {
                    "temperature": 0.5,
                    "maxOutputTokens": 800,
                    "topP": 0.95,
                },
            }
            if system_instruction:
                payload_dict["systemInstruction"] = {"parts": [{"text": system_instruction}]}

            headers: dict[str, str] = {
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
                "Authorization": f"Bearer {api_key}",
            }
            req = Request(
                url,
                data=json.dumps(payload_dict).encode("utf-8"),
                headers=headers,
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
            LOGGER.debug("Antigravity Gemini call in DebateEngine failed: %s", err)
        return None

    def _call_nvidia_nim(self, messages: list[dict[str, str]]) -> str | None:
        key = config.NVIDIA_API_KEY or os.getenv("NVIDIA_API_KEY", "")
        if not key:
            return None
        base = config.NVIDIA_BASE_URL or "https://integrate.api.nvidia.com/v1"
        endpoint = f"{base}/chat/completions"
        candidate_models = [
            "meta/llama-3.1-8b-instruct",
            "meta/llama-3.1-70b-instruct",
            "mistralai/mistral-large",
            config.NVIDIA_MODEL,
        ]
        for model in candidate_models:
            if not model:
                continue
            payload = {
                "model": model,
                "messages": messages,
                "temperature": 0.6,
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
                        "User-Agent": "KnightBot/1.0",
                    },
                    method="POST",
                )
                with urlopen(req, timeout=5) as response:
                    result = json.loads(response.read().decode("utf-8"))
                choices = result.get("choices")
                if choices and isinstance(choices, list):
                    content = choices[0].get("message", {}).get("content", "")
                    if content and content.strip():
                        return content.strip()
            except Exception as err:
                LOGGER.debug("NVIDIA NIM model %s failed in DebateEngine: %s", model, err)
                continue
        return None
