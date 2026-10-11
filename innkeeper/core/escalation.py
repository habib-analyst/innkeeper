"""Human-in-the-loop escalation policy.

Every action class maps to a tier:
  AUTO       — agent acts immediately, journaled.
  CONFIRM    — agent drafts the action, human taps approve in chat.
  HUMAN_ONLY — agent never acts; it drafts a briefing for a human.

Vertical skill packs declare their policy; the core enforces it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from innkeeper.core.ledger import EscalationTier


@dataclass
class EscalationPolicy:
    """tier_for(action, context) -> EscalationTier. Rules are evaluated in order."""

    defaults: Dict[str, EscalationTier] = field(default_factory=dict)
    # list of (predicate_name, action_prefix, tier) evaluated before defaults
    overrides: List[tuple] = field(default_factory=list)

    def tier_for(self, action: str, context: Optional[Dict] = None) -> EscalationTier:
        ctx = context or {}
        for pred_name, prefix, tier in self.overrides:
            if action.startswith(prefix) and self._pred(pred_name, ctx):
                return tier
        for prefix, tier in self.defaults.items():
            if action.startswith(prefix):
                return tier
        return EscalationTier.CONFIRM  # fail-closed: unknown actions need a human

    @staticmethod
    def _pred(name: str, ctx: Dict) -> bool:
        if name == "high_value":
            return float(ctx.get("amount_pkr", 0)) >= float(ctx.get("high_value_threshold_pkr", 100000))
        if name == "angry_guest":
            return bool(ctx.get("angry_guest"))
        if name == "overbooking_risk":
            return bool(ctx.get("overbooking_risk"))
        if name == "refund":
            return bool(ctx.get("is_refund"))
        return False


def hostel_policy() -> EscalationPolicy:
    """Day-one policy for the hostel vertical."""
    return EscalationPolicy(
        defaults={
            "message.send": EscalationTier.AUTO,       # answering inquiries, confirmations
            "booking.create": EscalationTier.AUTO,     # core automation: take the booking
            "booking.checkin": EscalationTier.AUTO,
            "booking.checkout": EscalationTier.AUTO,
            "task.create": EscalationTier.AUTO,        # housekeeping dispatch
            "task.done": EscalationTier.AUTO,
            "guest.upsert": EscalationTier.AUTO,
            "review.request": EscalationTier.AUTO,
            "payment.record": EscalationTier.CONFIRM,  # money movement: human confirms
            "booking.cancel": EscalationTier.CONFIRM,
            "booking.modify": EscalationTier.CONFIRM,
            "complaint.brief": EscalationTier.HUMAN_ONLY,  # owner resolves personally
            "digest.send": EscalationTier.AUTO,
        },
        overrides=[
            # big money or refunds always need a human, even if the action defaults lower
            ("high_value", "booking.", EscalationTier.CONFIRM),
            ("refund", "payment.", EscalationTier.HUMAN_ONLY),
            ("angry_guest", "message.", EscalationTier.CONFIRM),
            ("overbooking_risk", "booking.create", EscalationTier.CONFIRM),
        ],
    )
