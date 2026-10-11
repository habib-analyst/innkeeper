"""Transport interface: how the agent talks to the world.

WhatsApp is the universal UI. The interface is transport-agnostic so the same
agent core runs against the mock (simulation/tests), the WhatsApp Cloud API
(production), or any future channel.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class InboundMessage:
    sender: str          # phone / user id
    text: str
    ts: str = ""
    meta: Dict = field(default_factory=dict)


@dataclass
class OutboundMessage:
    to: str
    text: str
    kind: str = "text"   # text | template
    template: Optional[str] = None


class Transport:
    name = "base"

    def send(self, msg: OutboundMessage) -> str:
        """Send a message; returns a provider message id."""
        raise NotImplementedError

    def poll(self) -> List[InboundMessage]:
        """Return newly arrived inbound messages (mock/long-poll style)."""
        raise NotImplementedError

    def close(self) -> None:
        pass
