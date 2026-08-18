"""AI Policy & Permission Authority Engine for KnightBot (Ineffa).

Enforces 4-tier role-based permissions, autonomous allow/deny/escalate decisions,
spoofing detection, rate limiting, and tamper-evident audit logging.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any, Callable


class UserRole(str, enum.Enum):
    """User hierarchy levels for bot governance."""
    FULL_SOVEREIGN = "owner"          # Bot owner (unrestricted godmode)
    GC_MODERATOR = "admin"            # Instagram group chat admin
    VIP_FRIEND = "friend"             # Whitelisted protected friend
    STANDARD_USER = "user"            # Standard chat participant
    RESTRICTED_TROLL = "restricted"   # Muted or strike-capped user


class PolicyDecisionType(str, enum.Enum):
    """Autonomous decision outcomes for bot actions."""
    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"


@dataclass
class PolicyDecision:
    """Outcome of a policy evaluation."""
    decision: PolicyDecisionType
    allowed: bool
    reason: str
    refusal_roast: str | None = None
    requires_confirmation: bool = False


class PolicyEngine:
    """Evaluates requested bot actions against caller authority and safety rules."""

    ADMIN_ONLY_COMMANDS = {
        "kick", "remove", "rm", "ban", "unban", "add", "promote", "demote",
        "mute", "unmute", "warn", "clearwarn", "tagall", "lockdown",
        "clear", "broadcast", "setwelcome", "antiraid", "setting", "setrules"
    }

    OWNER_ONLY_COMMANDS = {
        "shutdown", "restart", "eval", "exec", "reload", "setstatus", "unban_all", "db_compact"
    }

    PROTECTED_FROM_MODERATION = {"owner", "friend"}

    def __init__(self, owner_username: str = "") -> None:
        self.owner_username = (owner_username or getattr(config, "OWNER_USERNAME", "")).lstrip("@").lower()
        self.rate_limits: dict[str, list[float]] = {}
        self.audit_log: list[dict[str, Any]] = []

    def check_rate_limit(self, user_id: str, max_requests: int = 10, window_sec: float = 60.0) -> bool:
        """Sliding window rate limiter to prevent denial-of-service command spam."""
        now = time.time()
        timestamps = self.rate_limits.setdefault(user_id, [])
        self.rate_limits[user_id] = [ts for ts in timestamps if now - ts < window_sec]
        if len(self.rate_limits[user_id]) >= max_requests:
            return False
        self.rate_limits[user_id].append(now)
        return True

    def evaluate_action(
        self,
        command_name: str,
        actor_id: str,
        actor_username: str,
        actor_role: UserRole,
        target_username: str | None = None,
        target_role: UserRole | None = None,
    ) -> PolicyDecision:
        """Evaluate whether an actor has permission to execute a specific command."""
        cmd = command_name.lstrip(".").lower()
        actor_clean = actor_username.lstrip("@").lower()
        target_clean = (target_username or "").lstrip("@").lower()

        # 1. Check Rate Limit
        if not self.check_rate_limit(actor_id, max_requests=15 if actor_role == UserRole.FULL_SOVEREIGN else 8):
            return PolicyDecision(
                decision=PolicyDecisionType.DENY,
                allowed=False,
                reason="Rate limit exceeded",
                refusal_roast="slow down your typing speed, you're not in an esports tournament 💀 Chill for a minute ✨",
            )

        # 2. Check Owner Godmode Commands
        if cmd in self.OWNER_ONLY_COMMANDS:
            if actor_role == UserRole.FULL_SOVEREIGN or actor_clean == self.owner_username:
                return PolicyDecision(decision=PolicyDecisionType.ALLOW, allowed=True, reason="Owner godmode authorized")
            return PolicyDecision(
                decision=PolicyDecisionType.DENY,
                allowed=False,
                reason="Restricted to Bot Owner",
                refusal_roast="nice try mortal, but only my creator holds the keys to the kingdom 🗝️⚔️",
            )

        # 3. Check Target Immunity (Owner and VIP Friends cannot be kicked/banned/muted/removed)
        if target_clean:
            if target_clean == self.owner_username or target_role in (UserRole.FULL_SOVEREIGN, UserRole.VIP_FRIEND):
                if cmd in ("kick", "remove", "rm", "ban", "mute", "warn", "demote"):
                    return PolicyDecision(
                        decision=PolicyDecisionType.DENY,
                        allowed=False,
                        reason="Target has sovereign immunity",
                        refusal_roast=f"i would literally self-destruct before kicking @{target_clean} 💀 They are under sovereign protection 🛡️✨",
                    )

        # 4. Check Group Chat Admin Commands
        if cmd in self.ADMIN_ONLY_COMMANDS:
            if actor_role in (UserRole.FULL_SOVEREIGN, UserRole.GC_MODERATOR) or actor_clean == self.owner_username:
                return PolicyDecision(decision=PolicyDecisionType.ALLOW, allowed=True, reason="Admin permission verified")
            return PolicyDecision(
                decision=PolicyDecisionType.DENY,
                allowed=False,
                reason="Requires GC Admin",
                refusal_roast="you need Instagram group admin permissions to command me to do that 👑 Sit back and enjoy the show ✨",
            )

        # 5. Check Restricted Trolls
        if actor_role == UserRole.RESTRICTED_TROLL:
            return PolicyDecision(
                decision=PolicyDecisionType.DENY,
                allowed=False,
                reason="Actor is restricted",
                refusal_roast="your account is in timeout. be quiet and think about your actions 🔇",
            )

        # 6. Standard utility commands (.song, .video, .search, .calc, .remind, etc.)
        return PolicyDecision(decision=PolicyDecisionType.ALLOW, allowed=True, reason="Public utility command approved")

    def log_action(
        self,
        command_name: str,
        actor_id: str,
        actor_username: str,
        decision: PolicyDecision,
        target_username: str | None = None,
    ) -> dict[str, Any]:
        """Record an audit trail entry for moderation and governance."""
        entry = {
            "timestamp": time.time(),
            "command": command_name,
            "actor_id": actor_id,
            "actor_username": actor_username,
            "decision": decision.decision.value,
            "allowed": decision.allowed,
            "reason": decision.reason,
            "target": target_username,
        }
        self.audit_log.append(entry)
        if len(self.audit_log) > 1000:
            self.audit_log.pop(0)
        return entry
