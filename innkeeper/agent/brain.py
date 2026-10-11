"""Conversation engine: deterministic NLU + slot state machine, LLM-swappable.

Day-one engine is rule-based (deterministic, testable, $0, no API keys).
`LLMProvider` is the seam: drop in any free-tier LLM later and only
`classify()` changes — routing, journaling and escalation stay identical.
The paper is explicit about which engine produced the measured numbers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Protocol


# ---------------------------------------------------------------------------
# LLM provider seam
# ---------------------------------------------------------------------------

class LLMProvider(Protocol):
    def complete(self, system: str, messages: List[Dict[str, str]]) -> str: ...


class StubProvider:
    """Deterministic stand-in: echoes a structured classification.

    Used in simulation/tests. A real provider (free-tier API) implements the
    same protocol; the agent never knows the difference.
    """
    def complete(self, system: str, messages: List[Dict[str, str]]) -> str:
        last = messages[-1]["content"] if messages else ""
        intent, slots = classify(last)
        return f"intent={intent} " + " ".join(f"{k}={v}" for k, v in slots.items())


# ---------------------------------------------------------------------------
# Deterministic NLU (rule-based, English + Roman Urdu keywords)
# ---------------------------------------------------------------------------

INTENT_PATTERNS: List[tuple] = [
    # "book <room>" as the FIRST word = confirming a quoted room (booking intent).
    # "book" mid-sentence ("I want to book a room") stays an availability inquiry.
    ("book", [r"^\s*book\b"]),
    ("cancel_booking", [r"\bcancel( my)? booking\b", r"\bcancel\b.*\broom\b"]),
    ("modify_booking", [r"\b(change|modify|shift|postpone|reschedule)\b.*\b(booking|date|room)\b",
                        r"\bdate.*\b(change|badal)"]),
    ("late_checkout", [r"\blate checkout\b", r"\blate check.out\b", r"\bcheckout.*late\b"]),
    ("checkout", [r"\bcheck.?out\b", r"\bchecking out\b", r"\bkhali kar", r"\bleaving\b"]),
    ("checkin", [r"\bcheck.?in\b", r"\bchecking in\b", r"\barriv", r"\bpohnch"]),
    ("housekeeping", [r"\bclean", r"\bsafai\b", r"\btowel", r"\bhousekeeping\b", r"\bextra (bed|pillow|blanket)\b"]),
    ("payment_info", [r"\bpay\b", r"\bpayment\b", r"\beasypaisa\b", r"\bjazzcash\b", r"\bbank\b", r"\bprice\b", r"\brate\b", r"\bcost\b", r"\bkitne\b.*\b(paise|pese|charges)\b"]),
    ("availability", [r"\bavailable\b", r"\bavailability\b", r"\broom\b.*\b(free|empty|khali)\b",
                      r"\bbook\b", r"\breserve\b", r"\bchahiye\b.*\broom\b", r"\bkya rate\b"]),
    ("review", [r"\breview\b", r"\brating\b", r"\bstar\b"]),
    ("complaint", [r"\bcomplaint\b", r"\bshikayat\b", r"\bterrible\b", r"\bawful\b",
                   r"\bangry\b", r"\brefund\b", r"\bworst\b", r"\bmanager\b", r"\bowner\b"]),
    ("greeting", [r"^(salam|assalam|hello|hi|hey|aoa)\b", r"\bsalaam\b"]),
]

DATE_PATTERNS = [
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})"), lambda m: f"{m.group(1)}-{m.group(2)}-{m.group(3)}"),
    (re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})"), lambda m: f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"),
]

RELATIVE_DATES = {
    "today": 0, "tonight": 0, "aaj": 0,
    "tomorrow": 1, "kal": 1,
    "day after tomorrow": 2, "parson": 2,
}


def extract_dates(text: str, today: Optional[date] = None) -> List[str]:
    today = today or date.today()
    found: List[str] = []
    low = text.lower()
    for word, delta in RELATIVE_DATES.items():
        if re.search(rf"\b{re.escape(word)}\b", low):
            found.append((today + timedelta(days=delta)).isoformat())
    for rx, fmt in DATE_PATTERNS:
        for m in rx.finditer(text):
            try:
                found.append(fmt(m))
            except ValueError:
                pass
    # "for 3 nights" style: duration from a start date
    dur = re.search(r"\bfor (\d+) nights?\b", low)
    if dur and found:
        start = date.fromisoformat(found[0])
        found.append((start + timedelta(days=int(dur.group(1)))).isoformat())
    # dedupe, keep order
    seen, out = set(), []
    for d in found:
        if d not in seen:
            seen.add(d); out.append(d)
    return out


def extract_guests(text: str) -> Optional[int]:
    m = re.search(r"\b(\d+)\s*(guests?|people|persons|afraad|log)\b", text.lower())
    if m:
        return int(m.group(1))
    m = re.search(r"\b(single|double|triple)\b", text.lower())
    if m:
        return {"single": 1, "double": 2, "triple": 3}[m.group(1)]
    return None


def extract_room_type(text: str) -> Optional[str]:
    low = text.lower()
    for rt in ("deluxe", "double", "single", "dorm", "family", "suite"):
        if rt in low:
            return rt
    return None


def detect_angry(text: str) -> bool:
    low = text.lower()
    return bool(re.search(r"\b(angry|furious|terrible|awful|worst|shikayat|complaint|refund|manager|owner|sue)\b", low)) \
        or text.count("!") >= 3


def classify(text: str, today: Optional[date] = None) -> tuple:
    """-> (intent, slots). Deterministic.

    A leading greeting never swallows a substantive intent: non-greeting
    intents are evaluated first; greeting only fires when nothing else did.
    """
    low = text.lower()
    slots: Dict[str, Any] = {}
    dates = extract_dates(text, today)
    if dates:
        slots["check_in"] = dates[0]
        if len(dates) > 1:
            slots["check_out"] = dates[1]
    guests = extract_guests(text)
    if guests:
        slots["guests"] = guests
    rt = extract_room_type(text)
    if rt:
        slots["room_type"] = rt
    if detect_angry(text):
        slots["angry_guest"] = True
    substantive = [(i, ps) for i, ps in INTENT_PATTERNS if i != "greeting"]
    for intent, patterns in substantive:
        for p in patterns:
            if re.search(p, low):
                return intent, slots
    for intent, patterns in INTENT_PATTERNS:
        if intent != "greeting":
            continue
        for p in patterns:
            if re.search(p, low):
                # dates + a room mention is an inquiry even with a greeting
                if not (dates and ("room" in low or rt)):
                    return intent, slots
    # booking intent without the word "book": dates + room mention
    if dates and ("room" in low or rt):
        return "availability", slots
    for intent, patterns in INTENT_PATTERNS:
        if intent == "greeting":
            for p in patterns:
                if re.search(p, low):
                    return intent, slots
    return "unknown", slots


# ---------------------------------------------------------------------------
# Per-guest conversation state (slot-filling state machine)
# ---------------------------------------------------------------------------

@dataclass
class GuestSession:
    sender: str
    state: str = "idle"          # idle | booking_dates | booking_confirm | ...
    slots: Dict[str, Any] = field(default_factory=dict)
    booking_id: Optional[str] = None


class SessionStore:
    def __init__(self):
        self._sessions: Dict[str, GuestSession] = {}

    def get(self, sender: str) -> GuestSession:
        return self._sessions.setdefault(sender, GuestSession(sender=sender))

    def reset(self, sender: str) -> None:
        self._sessions.pop(sender, None)
