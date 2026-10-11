"""Skill-pack interface: verticals plug in here.

A skill pack declares: the backend schema name, seed data, message templates,
the escalation policy, and intent handlers. The agent core (routing, ledger,
transport) never changes between verticals.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from innkeeper.core.escalation import EscalationPolicy


@dataclass
class BusinessProfile:
    name: str
    vertical: str = "hostel"
    owner_phone: str = ""
    staff_group: str = ""            # WhatsApp group id for staff dispatch
    payment_methods: Dict[str, str] = field(default_factory=dict)  # method -> account detail
    review_link: str = ""            # Google review link (QR pipeline target)
    checkin_time: str = "14:00"
    checkout_time: str = "12:00"
    late_checkout_fee_pkr: int = 1500
    high_value_threshold_pkr: int = 100000


@dataclass
class SkillPack:
    name: str
    profile: BusinessProfile
    policy: EscalationPolicy
    seed_rooms: List[Dict[str, Any]] = field(default_factory=list)
    # intent -> handler(app, sender, slots, session) -> None
    handlers: Dict[str, Callable] = field(default_factory=dict)
