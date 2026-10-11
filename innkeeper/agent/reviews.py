"""Review engine: post-stay follow-up wired to the review pipeline.

After checkout, the scheduler fires a review request the next morning. This
plugs into Habib's existing QR-code review concept: the message carries the
Google review link (the same link the QR codes encode), so chat and physical
touchpoints converge on one funnel.
"""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

from innkeeper.core.ledger import EscalationTier
from innkeeper.transport.base import OutboundMessage


def schedule_request(app, booking_id: str, guest_phone: str) -> str:
    review_id = "rv" + uuid.uuid4().hex[:8]
    app.backend.insert("reviews", {
        "id": review_id, "booking_id": booking_id,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "responded": 0, "rating": None, "channel": "google",
    })
    app.ledger.record(
        actor="agent:innkeeper", action="review.request", tier=EscalationTier.AUTO,
        before={}, after={"review_id": review_id, "booking_id": booking_id},
        undo={"action": "review.request", "review_id": review_id},
        note=f"review request scheduled for {booking_id}",
    )
    return review_id


def send_due_requests(app, today: str) -> list:
    """Send review requests for checkouts that haven't been asked yet. Returns message texts."""
    rows = app.backend.query(
        """SELECT r.id AS review_id, b.id AS booking_id, g.phone AS phone, g.name AS name
           FROM reviews r
           JOIN bookings b ON b.id = r.booking_id
           JOIN guests g ON g.id = b.guest_id
           WHERE r.responded = 0 AND date(r.requested_at) <= date(?)
             AND NOT EXISTS (
               SELECT 1 FROM _journal j
               WHERE j.action='review.request.sent' AND json_extract(j.after_json,'$.review_id') = r.id
             )""",
        [today],
    )
    sent = []
    link = app.pack.profile.review_link
    for r in rows:
        text = (f"Hi {r['name']}! 👋 Thanks again for staying at {app.pack.profile.name}.\n"
                f"If you have 30 seconds, a Google review helps us enormously:\n{link}\n"
                "— Team " + app.pack.profile.name)
        msg_id = app.transport.send(OutboundMessage(to=r["phone"], text=text))
        app.ledger.record(
            actor="agent:innkeeper", action="review.request.sent", tier=EscalationTier.AUTO,
            before={}, after={"review_id": r["review_id"], "msg_id": msg_id},
            undo={"action": "message.send", "msg_id": msg_id},
        )
        sent.append(text)
    return sent


def record_response(app, booking_id: str, rating: int | None = None) -> None:
    row = app.backend.query_one("SELECT * FROM reviews WHERE booking_id=? ORDER BY requested_at DESC LIMIT 1",
                                [booking_id])
    if row:
        app.backend.update("reviews", "id=?", [row["id"]], {"responded": 1, "rating": rating})
