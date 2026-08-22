"""10-Minute Periodic Full Abuse Digest Reporter Service.
Continuously audits group chat violations and compiles structured reports for owners.
"""
from __future__ import annotations

import datetime
import logging
import threading
import time
from collections import Counter
from typing import Any, Callable

LOGGER = logging.getLogger("jinshi_mds")


class AbuseReporter:
    """Automated periodic abuse auditor generating 10-minute digests."""

    def __init__(
        self,
        database: Any,
        dispatch_callback: Callable[[str | int, str], None] | None = None,
        owner_username: str = "",
        interval_seconds: float = 600.0,
    ) -> None:
        self.database = database
        self.dispatch_callback = dispatch_callback
        self.owner_username = str(owner_username).lstrip("@")
        self.interval_seconds = max(60.0, float(interval_seconds))
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_report_time = time.time()
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="AbuseDigestReporter")
            self._thread.start()
            LOGGER.info("10-minute Abuse Digest Reporter started (interval=%ss)", self.interval_seconds)

    def stop(self) -> None:
        with self._lock:
            self._running = False

    def _run_loop(self) -> None:
        while self._running:
            time.sleep(self.interval_seconds)
            if not self._running:
                break
            try:
                self.check_and_dispatch_digest(minutes=10)
            except Exception as err:
                LOGGER.warning("Error in 10-minute abuse digest loop: %s", err)

    def generate_digest(self, minutes: int = 10) -> tuple[int, str]:
        """Compile a structured 10-minute abuse digest report."""
        if not hasattr(self.database, "get_reports_in_timeframe"):
            # Fallback to pending reports if timeframe method not yet available
            reports = getattr(self.database, "get_pending_reports", lambda: [])(limit=30)
        else:
            reports = self.database.get_reports_in_timeframe(minutes=minutes, limit=50)

        if not reports:
            return 0, f"✅ No abuse or rule violations detected in the last {minutes} minutes. All group chats are clean! ✨"

        now_str = datetime.datetime.now().strftime("%I:%M %p")
        total_count = len(reports)

        offenders = Counter(r.get("offender_username", "unknown") for r in reports)
        rules = Counter(r.get("rule_broken", "Violation") for r in reports)

        lines = [
            f"🚨 [10-MINUTE ABUSE & VIOLATIONS DIGEST] 🚨",
            f"⏱️ Generated at: {now_str} (Last {minutes}m)",
            f"📊 Total Incidents Logged: {total_count}\n",
            "👥 Top Offenders:",
        ]
        for user, count in offenders.most_common(5):
            lines.append(f"  • @{user.lstrip('@')}: {count} violation(s)")

        lines.append("\n📋 Rule Breakdown:")
        for rule, count in rules.most_common(5):
            lines.append(f"  • {rule}: {count} incident(s)")

        lines.append("\n📝 Recent Incident Logs:")
        for idx, r in enumerate(reports[:6], 1):
            u = r.get("offender_username", "unknown")
            rule_name = r.get("rule_broken", "Unknown")
            reason_text = r.get("reason", "N/A")
            snippet = r.get("snippet", "")
            t_id = r.get("thread_id", "GC")
            lines.append(f"{idx}. @{u.lstrip('@')} in Thread #{t_id}")
            lines.append(f"   • Rule: {rule_name}")
            lines.append(f"   • Reason: {reason_text}")
            if snippet:
                lines.append(f"   • Snippet: \"{snippet[:80]}\"")

        if total_count > 6:
            lines.append(f"\n... and {total_count - 6} more incidents recorded in database.")

        lines.append("\n💡 Use .reports to view pending reports or .resolve <id> to resolve.")
        return total_count, "\n".join(lines)

    def check_and_dispatch_digest(self, minutes: int = 10) -> None:
        """Check for recent abuses and dispatch digest to owner if violations were recorded."""
        count, digest_text = self.generate_digest(minutes=minutes)
        if count > 0 and self.dispatch_callback and self.owner_username:
            LOGGER.info("Dispatching 10-minute abuse digest (%d incidents) to @%s", count, self.owner_username)
            try:
                self.dispatch_callback(self.owner_username, digest_text)
            except Exception as err:
                LOGGER.warning("Failed to send 10-min abuse digest DM: %s", err)
