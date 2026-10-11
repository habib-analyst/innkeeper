"""InnkeeperApp: the wired-up agent.

Backend (generated SQLite) + transaction ledger + escalation policy +
transport + vertical skill pack + conversation sessions. One loop:
poll transport -> classify -> route to skill handler -> journal -> reply.
"""
from __future__ import annotations

import time
from datetime import date
from typing import Dict, List, Optional

from innkeeper.agent import brain
from innkeeper.agent.brain import SessionStore
from innkeeper.core.backend import BusinessBackend
from innkeeper.core.escalation import EscalationPolicy
from innkeeper.core.ledger import TransactionLedger
from innkeeper.core.scheduler import Scheduler
from innkeeper.skills.base import SkillPack
from innkeeper.transport.base import Transport


class InnkeeperApp:
    def __init__(
        self,
        backend: BusinessBackend,
        pack: SkillPack,
        transport: Transport,
        provider=None,
    ):
        self.backend = backend
        self.pack = pack
        self.policy: EscalationPolicy = pack.policy
        self.transport = transport
        self.provider = provider or brain.StubProvider()
        self.ledger = TransactionLedger(backend)
        self.sessions = SessionStore()
        self.pending_confirmations: Dict[str, Dict] = {}
        self.scheduler = Scheduler(self)
        self._wire_scheduler()

    # -- scheduler jobs ------------------------------------------------------
    def _wire_scheduler(self) -> None:
        from innkeeper.agent import digest as digest_mod
        from innkeeper.agent import reviews as reviews_mod

        self.scheduler.every_day("review_requests",
                                 lambda: reviews_mod.send_due_requests(self, date.today().isoformat()))
        self.scheduler.every_day("owner_digest",
                                 lambda: [digest_mod.send_digest(self, date.today().isoformat())])

    # -- main loop -----------------------------------------------------------
    def handle_message(self, sender: str, text: str) -> List[str]:
        """Process one inbound message. Returns the reply texts sent."""
        from innkeeper.skills.hostel import pack as hostel_pack  # local import: owner-approve lives there
        session = self.sessions.get(sender)
        replies_before = len(self.transport.sent)

        # owner commands (APPROVE/REJECT) take precedence
        if sender == self.pack.profile.owner_phone:
            if hostel_pack.handle_owner_approve(self, sender, {"raw_text": text}, session):
                return self._new_replies(replies_before)

        intent, slots = brain.classify(text)
        slots["raw_text"] = text

        # "book X" while in booking_confirm state carries the room name
        if intent == "unknown" and session.state == "booking_confirm" and "book" in text.lower():
            intent = "book"

        handler = self.pack.handlers.get(intent, self.pack.handlers["unknown"])
        t0 = time.perf_counter()
        handler(self, sender, slots, session)
        dt_ms = (time.perf_counter() - t0) * 1000
        self._last_latency_ms = dt_ms
        return self._new_replies(replies_before)

    def _new_replies(self, before: int) -> List[str]:
        return [m.text for m in self.transport.sent[before:]]

    def pump(self) -> int:
        """Poll transport and handle all pending inbound messages. Returns count handled."""
        n = 0
        for msg in self.transport.poll():
            self.handle_message(msg.sender, msg.text)
            n += 1
        return n

    def run_scheduler(self, today: Optional[str] = None) -> Dict[str, List[str]]:
        return self.scheduler.run_due(today)

    def close(self) -> None:
        self.transport.close()
        self.backend.close()
