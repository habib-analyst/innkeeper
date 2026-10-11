"""Mock transport: in-memory WhatsApp stand-in for simulation, demos and tests.

Scripted guests push InboundMessages in; the agent's OutboundMessages are
captured for assertions and transcript rendering. Zero network, $0, instant.
"""
from __future__ import annotations

import itertools
from datetime import datetime, timezone
from typing import Dict, List

from innkeeper.transport.base import InboundMessage, OutboundMessage, Transport


class MockTransport(Transport):
    name = "mock"

    def __init__(self):
        self.inbox: List[InboundMessage] = []
        self.sent: List[OutboundMessage] = []
        self._ids = itertools.count(1)

    # -- guest side (the simulation harness drives this) ---------------------
    def guest_says(self, sender: str, text: str) -> None:
        self.inbox.append(
            InboundMessage(
                sender=sender, text=text,
                ts=datetime.now(timezone.utc).isoformat(),
            )
        )

    # -- agent side ----------------------------------------------------------
    def send(self, msg: OutboundMessage) -> str:
        self.sent.append(msg)
        return f"mock-{next(self._ids)}"

    def poll(self) -> List[InboundMessage]:
        batch, self.inbox = self.inbox, []
        return batch

    def transcript(self) -> str:
        """Render a WhatsApp-style conversation transcript (for the README demo)."""
        lines = []
        # interleave is approximate: show all guest msgs then agent replies in order.
        # The eval harness keeps per-scenario ordering; this is for humans.
        for m in self.sent:
            lines.append(f"🏠 innkeeper → {m.to}: {m.text}")
        return "\n".join(lines)

    def last_to(self, sender: str) -> str:
        for m in reversed(self.sent):
            if m.to == sender:
                return m.text
        return ""
