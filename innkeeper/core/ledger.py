"""Reversible transaction journal — "double-entry bookkeeping for agents".

The reason businesses don't let agents ACT is fear of mistakes. Every agent
action is journaled here as a reversible transaction against business state:
before/after snapshots, an undo operation, the escalation tier it ran under.
Attributable, auditable, undoable.

The journal itself lives in the business backend database (table
`_journal`), so the audit trail travels with the business's data.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from innkeeper.core.backend import BusinessBackend


class EscalationTier(str, Enum):
    AUTO = "auto"            # agent acts, journaled
    CONFIRM = "confirm"      # agent proposes, human taps approve in chat
    HUMAN_ONLY = "human_only"  # agent never acts; drafts for a human


@dataclass
class JournalEntry:
    id: str
    ts: str
    actor: str                     # agent / human:<name>
    action: str                    # e.g. "booking.create"
    tier: str                      # auto / confirm / human_only
    status: str                    # done / proposed / approved / reverted / rejected
    before: Dict[str, Any]
    after: Dict[str, Any]
    undo: Dict[str, Any]           # machine-readable inverse op
    note: str = ""


UNDO_HANDLERS: Dict[str, Callable[[BusinessBackend, Dict[str, Any]], None]] = {}


def undo_handler(action: str):
    """Register the inverse operation for an action type."""
    def deco(fn: Callable[[BusinessBackend, Dict[str, Any]], None]):
        UNDO_HANDLERS[action] = fn
        return fn
    return deco


class TransactionLedger:
    def __init__(self, backend: BusinessBackend):
        self.backend = backend
        backend.conn.execute(
            """CREATE TABLE IF NOT EXISTS _journal (
                id TEXT PRIMARY KEY, ts TEXT NOT NULL, actor TEXT NOT NULL,
                action TEXT NOT NULL, tier TEXT NOT NULL, status TEXT NOT NULL,
                before_json TEXT NOT NULL, after_json TEXT NOT NULL,
                undo_json TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
            )"""
        )
        backend.conn.commit()

    # -- recording -----------------------------------------------------------
    def record(
        self,
        actor: str,
        action: str,
        tier: EscalationTier | str,
        before: Dict[str, Any],
        after: Dict[str, Any],
        undo: Dict[str, Any],
        note: str = "",
        status: str = "done",
    ) -> JournalEntry:
        entry = JournalEntry(
            id="j" + uuid.uuid4().hex[:12],
            ts=datetime.now(timezone.utc).isoformat(),
            actor=actor,
            action=action,
            tier=tier.value if isinstance(tier, EscalationTier) else tier,
            status=status,
            before=before,
            after=after,
            undo=undo,
            note=note,
        )
        self.backend.conn.execute(
            """INSERT INTO _journal
               (id, ts, actor, action, tier, status, before_json, after_json, undo_json, note)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (entry.id, entry.ts, entry.actor, entry.action, entry.tier, entry.status,
             json.dumps(entry.before), json.dumps(entry.after), json.dumps(entry.undo), entry.note),
        )
        self.backend.conn.commit()
        return entry

    def get(self, entry_id: str) -> Optional[JournalEntry]:
        row = self.backend.query_one("SELECT * FROM _journal WHERE id=?", [entry_id])
        return self._row_to_entry(row) if row else None

    def list(self, action: Optional[str] = None, status: Optional[str] = None, limit: int = 200) -> List[JournalEntry]:
        q = "SELECT * FROM _journal"
        clauses, args = [], []
        if action:
            clauses.append("action=?"); args.append(action)
        if status:
            clauses.append("status=?"); args.append(status)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        return [self._row_to_entry(r) for r in self.backend.query(q, args)]

    @staticmethod
    def _row_to_entry(r) -> JournalEntry:
        return JournalEntry(
            id=r["id"], ts=r["ts"], actor=r["actor"], action=r["action"],
            tier=r["tier"], status=r["status"],
            before=json.loads(r["before_json"]), after=json.loads(r["after_json"]),
            undo=json.loads(r["undo_json"]), note=r["note"],
        )

    # -- reversal ------------------------------------------------------------
    def revert(self, entry_id: str, actor: str = "human:owner") -> JournalEntry:
        """Undo a journaled action. The revert itself is journaled (audit trail is append-only)."""
        entry = self.get(entry_id)
        if entry is None:
            raise KeyError(f"no journal entry {entry_id}")
        if entry.status == "reverted":
            raise ValueError(f"entry {entry_id} already reverted")
        handler = UNDO_HANDLERS.get(entry.action)
        if handler is None:
            raise ValueError(f"action {entry.action!r} has no registered undo — cannot revert safely")
        handler(self.backend, entry.undo)
        self.backend.conn.execute("UPDATE _journal SET status='reverted' WHERE id=?", [entry_id])
        self.backend.conn.commit()
        return self.record(
            actor=actor, action=f"{entry.action}.revert", tier=EscalationTier.HUMAN_ONLY,
            before=entry.after, after=entry.before,
            undo={"reapply": entry.id}, note=f"reverted {entry_id}", status="done",
        )

    # -- anomaly scan (feeds the owner digest) --------------------------------
    def anomalies(self, limit: int = 50) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for e in self.list(limit=limit):
            if e.status == "reverted":
                out.append({"kind": "reverted_action", "entry": e.id, "action": e.action, "ts": e.ts})
            if e.tier == EscalationTier.HUMAN_ONLY.value and e.status == "done" and e.actor.startswith("agent"):
                out.append({"kind": "tier_violation", "entry": e.id, "action": e.action, "ts": e.ts})
        return out


# ---------------------------------------------------------------------------
# Undo implementations for the hostel vertical's actions.
# ---------------------------------------------------------------------------

@undo_handler("booking.create")
def _undo_booking_create(backend: BusinessBackend, undo: Dict[str, Any]) -> None:
    backend.update("bookings", "id=?", [undo["booking_id"]], {"status": "cancelled"})


@undo_handler("booking.cancel")
def _undo_booking_cancel(backend: BusinessBackend, undo: Dict[str, Any]) -> None:
    backend.update("bookings", "id=?", [undo["booking_id"]], {"status": undo["prev_status"]})


@undo_handler("task.create")
def _undo_task_create(backend: BusinessBackend, undo: Dict[str, Any]) -> None:
    backend.conn.execute("DELETE FROM staff_tasks WHERE id=?", [undo["task_id"]])
    backend.conn.commit()


@undo_handler("task.done")
def _undo_task_done(backend: BusinessBackend, undo: Dict[str, Any]) -> None:
    backend.update("staff_tasks", "id=?", [undo["task_id"]], {"status": "open"})


@undo_handler("guest.upsert")
def _undo_guest_upsert(backend: BusinessBackend, undo: Dict[str, Any]) -> None:
    if undo.get("created"):
        backend.conn.execute("DELETE FROM guests WHERE id=?", [undo["guest_id"]])
        backend.conn.commit()
    else:
        backend.update("guests", "id=?", [undo["guest_id"]], undo["prev"])


@undo_handler("payment.record")
def _undo_payment_record(backend: BusinessBackend, undo: Dict[str, Any]) -> None:
    backend.update("payments", "id=?", [undo["payment_id"]], {"status": "refunded"})


@undo_handler("message.send")
def _undo_message_send(backend: BusinessBackend, undo: Dict[str, Any]) -> None:
    # Messages can't be unsent on WhatsApp after delivery; the undo is
    # journaling the correction. Marking the entry reverted is the audit.
    pass
