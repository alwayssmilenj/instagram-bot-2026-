"""Message burst debouncer and coalescer for KnightBot (Ineffa).

Combines multi-part consecutive messages from the same sender into a single unified thought,
preventing the bot from spamming separate replies to every split fragment.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

LOGGER = logging.getLogger("knightbot.debouncer")


class MessageBurstDebouncer:
    """Thread-safe multi-part message coalescer."""

    def __init__(self, debounce_seconds: float = 0.8, max_burst_seconds: float = 3.0) -> None:
        self.debounce_seconds = max(0.0, float(debounce_seconds))
        self.max_burst_seconds = max(self.debounce_seconds, float(max_burst_seconds))
        self._lock = threading.Lock()
        self._active: dict[tuple[str, str], dict[str, Any]] = {}

    def ingest(self, thread_id: str | int, sender_id: str | int, text: str) -> tuple[bool, str]:
        """Ingest a message fragment.

        Returns:
            (is_leader, final_coalesced_text)
            - If is_leader is True: the calling thread is the designated processor for the final coalesced text.
            - If is_leader is False: this fragment was successfully buffered; the caller should return immediately.
        """
        if self.debounce_seconds <= 0.0 or not text.strip():
            return True, text

        key = (str(thread_id), str(sender_id))
        now = time.monotonic()

        with self._lock:
            if key in self._active:
                # Existing leader is currently waiting; append this fragment
                self._active[key]["texts"].append(text.strip())
                self._active[key]["last_time"] = now
                LOGGER.debug("Coalesced message fragment from sender %s in thread %s: %s", sender_id, thread_id, text[:40])
                return False, ""

            # Become the burst leader
            entry = {
                "texts": [text.strip()],
                "start_time": now,
                "last_time": now,
            }
            self._active[key] = entry

        # Burst leader waits for silence window or maximum burst cutoff
        try:
            while True:
                with self._lock:
                    if key not in self._active:
                        break
                    entry = self._active[key]
                    time_since_last = time.monotonic() - entry["last_time"]
                    total_duration = time.monotonic() - entry["start_time"]
                    wait_needed = self.debounce_seconds - time_since_last

                if wait_needed <= 0.02 or total_duration >= self.max_burst_seconds:
                    with self._lock:
                        final_texts = list(entry["texts"])
                        self._active.pop(key, None)
                    combined = " ".join(t for t in final_texts if t)
                    if len(final_texts) > 1:
                        LOGGER.info("Successfully merged %d message fragments into 1 thought for @%s: %s", len(final_texts), sender_id, combined[:80])
                    return True, combined

                time.sleep(max(0.02, min(wait_needed, 0.15)))
        finally:
            with self._lock:
                self._active.pop(key, None)

        return True, text
