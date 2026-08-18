"""Owner-only local siren with cooldown and volume restoration."""
from __future__ import annotations

import logging
import math
import re
import subprocess
import threading
import time
import wave
from pathlib import Path

from settings import DATA_DIR

LOGGER = logging.getLogger("jinshi_mds.homealert")


class HomeAlertService:
    def __init__(self, duration: float = 6.0, volume_percent: int = 120, cooldown: int = 60) -> None:
        self.duration = min(8.0, max(2.0, duration))
        self.volume_percent = min(150, max(25, volume_percent))
        self.cooldown = max(30, cooldown)
        self.last_trigger = 0.0
        self.lock = threading.Lock()
        self.siren_path = DATA_DIR / "homealert-siren.wav"
        self._ensure_siren()

    def _ensure_siren(self) -> None:
        if self.siren_path.exists() and self.siren_path.stat().st_size > 1000:
            return
        self.siren_path.parent.mkdir(parents=True, exist_ok=True)
        sample_rate = 44_100
        frames = bytearray()
        phase = 0.0
        total = int(sample_rate * self.duration)
        for index in range(total):
            frequency = 700 if int(index / sample_rate / 0.35) % 2 == 0 else 950
            phase += 2 * math.pi * frequency / sample_rate
            edge = min(1.0, index / (sample_rate * 0.05), (total - index) / (sample_rate * 0.05))
            sample = int(32767 * 0.22 * edge * math.sin(phase))
            frames.extend(sample.to_bytes(2, "little", signed=True))
        temporary = self.siren_path.with_suffix(".tmp")
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(1); output.setsampwidth(2); output.setframerate(sample_rate); output.writeframes(frames)
        temporary.replace(self.siren_path)

    @staticmethod
    def _volume_state() -> tuple[float, bool]:
        result = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True, timeout=3, check=True)
        match = re.search(r"Volume:\s*([0-9.]+)", result.stdout)
        if not match:
            raise RuntimeError("Could not read the default audio volume")
        return float(match.group(1)), "[MUTED]" in result.stdout

    def trigger(self) -> tuple[bool, str]:
        with self.lock:
            remaining = self.cooldown - (time.monotonic() - self.last_trigger)
            if remaining > 0:
                return False, f"Home alert cooldown: {int(remaining) + 1}s remaining."
            self.last_trigger = time.monotonic()
        threading.Thread(target=self._play, name="homealert-siren", daemon=True).start()
        return True, f"🚨 Home alert started for {self.duration:.0f}s at {self.volume_percent}% volume."

    def _play(self) -> None:
        previous_volume = 0.5
        was_muted = False
        try:
            previous_volume, was_muted = self._volume_state()
            subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"], timeout=3, check=True)
            subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{self.volume_percent}%"], timeout=3, check=True)
            LOGGER.warning("Owner home alert activated")
            subprocess.run(["pw-play", str(self.siren_path)], timeout=self.duration + 3, check=True)
        except Exception:
            LOGGER.exception("Home alert playback failed")
        finally:
            try:
                subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", str(previous_volume)], timeout=3, check=False)
                if was_muted:
                    subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"], timeout=3, check=False)
            except Exception:
                LOGGER.exception("Could not restore audio state after home alert")
