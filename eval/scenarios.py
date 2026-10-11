"""Scripted evaluation scenarios: deterministic guest conversations with labels.

Each scenario: a script of (sender, text) turns, optional owner replies, and
expectations used to compute the measured metrics:
  - booking conversion: inquiries with real availability -> confirmed booking
  - escalation precision/recall: expected vs actual tier per action
  - journal reversibility: checked separately in run_eval
"""
from __future__ import annotations

from typing import Any, Dict, List

GUEST_A = "+923001111111"
GUEST_B = "+923002222222"
GUEST_C = "+923003333333"
GUEST_D = "+923004444444"
OWNER = "+929990000000"


# Fixed anchor so scenarios are deterministic regardless of run date.
from datetime import date as _date, timedelta as _td
ANCHOR = _date(2026, 11, 10)


def D(offset: int) -> str:
    return (ANCHOR + _td(days=offset)).isoformat()


SCENARIOS: List[Dict[str, Any]] = [
    {
        "id": "happy_path",
        "desc": "inquiry -> availability -> book -> pay info -> checkin -> housekeeping -> checkout",
        "script": [
            (GUEST_A, f"Salam, room for 2 people from {D(1)} to {D(3)}?"),
            (GUEST_A, "book Deluxe 1"),
            (GUEST_A, "how do I pay?"),
            (GUEST_A, "arrived, checking in"),
            (GUEST_A, "need extra towels, please clean the room"),
            (GUEST_A, "checking out, thanks!"),
        ],
        "expect": {"booking_created": True, "tier_booking_create": "auto",
                   "housekeeping_task": True, "review_scheduled": True},
    },
    {
        "id": "no_availability",
        "desc": "all rooms pre-booked; guest must get a graceful fully-booked reply",
        "setup": "fill_all_rooms",
        "script": [(GUEST_B, f"Hi, any room {D(1)} to {D(3)}?")],
        "expect": {"booking_created": False, "reply_contains": "fully booked"},
    },
    {
        "id": "modify_approved",
        "desc": "guest shifts dates; CONFIRM tier; owner approves",
        "script": [
            (GUEST_A, f"Salam, room for 2 from {D(1)} to {D(3)}?"),
            (GUEST_A, "book Deluxe 1"),
            (GUEST_A, f"actually shift my booking to {D(5)} to {D(7)}"),
        ],
        "owner_script": ["APPROVE {prop}"],
        "expect": {"booking_created": True, "tier_booking_modify": "confirm",
                   "modified": True},
    },
    {
        "id": "cancel_approved",
        "desc": "guest cancels; CONFIRM tier; owner approves",
        "script": [
            (GUEST_A, f"Salam, room for 2 from {D(1)} to {D(3)}?"),
            (GUEST_A, "book Deluxe 1"),
            (GUEST_A, "cancel my booking please"),
        ],
        "owner_script": ["APPROVE {prop}"],
        "expect": {"tier_booking_cancel": "confirm", "cancelled": True},
    },
    {
        "id": "cancel_rejected",
        "desc": "guest cancels; owner REJECTS; booking must stay confirmed",
        "script": [
            (GUEST_A, f"Salam, room for 2 from {D(1)} to {D(3)}?"),
            (GUEST_A, "book Deluxe 1"),
            (GUEST_A, "cancel my booking please"),
        ],
        "owner_script": ["REJECT {prop}"],
        "expect": {"cancelled": False, "booking_status": "confirmed"},
    },
    {
        "id": "late_checkout",
        "desc": "late checkout fee quote and acceptance",
        "script": [
            (GUEST_A, f"Salam, room for 2 from {D(1)} to {D(3)}?"),
            (GUEST_A, "book Deluxe 1"),
            (GUEST_A, "arrived, checking in"),
            (GUEST_A, "can I get late checkout?"),
            (GUEST_A, "yes"),
        ],
        "expect": {"reply_contains": "late checkout added"},
    },
    {
        "id": "complaint_escalates",
        "desc": "angry guest: empathy now, HUMAN_ONLY briefing to owner",
        "script": [
            (GUEST_C, "This is terrible!!! The room is awful and nobody answers! I want a refund!!!"),
        ],
        "expect": {"tier_complaint_brief": "human_only", "owner_notified": True,
                   "reply_contains": "manager"},
    },
    {
        "id": "high_value_confirm",
        "desc": "15-night deluxe stay (Rs 127,500) exceeds threshold -> CONFIRM tier",
        "script": [
            (GUEST_D, f"Salam, deluxe room for 15 nights from {D(1)} to {D(16)}"),
            (GUEST_D, "book Deluxe 1"),
        ],
        "owner_script": ["APPROVE {prop}"],
        "expect": {"tier_booking_create": "confirm", "booking_created": True},
    },
    {
        "id": "no_double_booking",
        "desc": "race: A quotes Deluxe 1, B books it, A confirms -> A must NOT double-book",
        "script": [
            (GUEST_A, f"Salam, room from {D(1)} to {D(3)}?"),
            (GUEST_B, f"Salam, room from {D(1)} to {D(3)}?"),
            (GUEST_B, "book Deluxe 1"),
            (GUEST_A, "book Deluxe 1"),
        ],
        "expect": {"deluxe1_bookings": 1, "no_double_booking": True},
    },
    {
        "id": "unknown_fallback",
        "desc": "gibberish gets a helpful fallback, no crash",
        "script": [(GUEST_B, "zxqwv blorpt fnord")],
        "expect": {"reply_contains": "not sure I caught that"},
    },
]
