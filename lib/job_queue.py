"""Bounded priority work queue with memory-pressure protection."""
from __future__ import annotations

import gc
import itertools
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

LOGGER = logging.getLogger("knightbot.queue")


def rss_bytes() -> int:
    try:
        with open("/proc/self/statm", encoding="utf-8") as statm:
            pages = int(statm.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return 0


@dataclass(order=True)
class WorkItem:
    priority: int
    sequence: int
    callback: Callable[[], None] | None = field(compare=False, default=None)


@dataclass(frozen=True)
class QueueReceipt:
    number: int
    waiting: int
    memory_pressure: bool


class PriorityWorkQueue:
    def __init__(self, workers: int, maximum: int, max_rss_bytes: int, emergency_exit: Callable[[], None]) -> None:
        self.workers = workers
        self.max_rss_bytes = max_rss_bytes
        self.emergency_exit = emergency_exit
        self.queue: queue.PriorityQueue[WorkItem] = queue.PriorityQueue(maxsize=maximum)
        self.counter = itertools.count(1)
        self.running = threading.Event()
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        self.running.set()
        for number in range(self.workers):
            thread = threading.Thread(target=self._worker, name=f"command-worker-{number + 1}", daemon=True)
            thread.start()
            self.threads.append(thread)
        monitor_thread = threading.Thread(target=self._memory_monitor, name="memory-guard", daemon=True)
        monitor_thread.start()
        self.threads.append(monitor_thread)

    def submit(self, callback: Callable[[], None], admin: bool = False) -> QueueReceipt:
        sequence = next(self.counter)
        pressure = rss_bytes() >= self.max_rss_bytes
        item = WorkItem(priority=0 if admin else 10, sequence=sequence, callback=callback)
        self.queue.put_nowait(item)
        return QueueReceipt(sequence, self.queue.qsize(), pressure)

    def _worker(self) -> None:
        while self.running.is_set():
            try:
                item = self.queue.get(timeout=1)
            except queue.Empty:
                continue
            try:
                if item.callback:
                    item.callback()
            except Exception:
                LOGGER.exception("Command queue job %s failed", item.sequence)
            finally:
                self.queue.task_done()
                item.callback = None
                item = None

    def _memory_monitor(self) -> None:
        consecutive = 0
        while self.running.is_set():
            used = rss_bytes()
            if used >= self.max_rss_bytes:
                gc.collect()
                used = rss_bytes()
            consecutive = consecutive + 1 if used >= self.max_rss_bytes else 0
            if consecutive >= 3:
                LOGGER.critical("RSS exceeded %d MiB; requesting supervised restart", self.max_rss_bytes // 1024 // 1024)
                self.emergency_exit()
                return
            self.running.wait(5.0)

    def stop(self, timeout: float = 3.0) -> None:
        self.running.clear()
        deadline = time.monotonic() + timeout
        for thread in self.threads:
            remaining = max(0.05, deadline - time.monotonic())
            thread.join(timeout=remaining)
        while not self.queue.empty():
            try:
                item = self.queue.get_nowait()
                item.callback = None
                self.queue.task_done()
            except queue.Empty:
                break
