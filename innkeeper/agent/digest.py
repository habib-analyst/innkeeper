"""Owner digest: the daily P&L-in-chat.

Every morning the owner gets one WhatsApp message: occupancy, revenue,
arrivals/departures, open staff tasks, ledger anomalies. Rendered as a
WhatsApp-formatted message and printable via CLI.
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List

from innkeeper.core.ledger import EscalationTier
from innkeeper.transport.base import OutboundMessage


def build_digest(app, today: str | None = None) -> str:
    today = today or date.today().isoformat()
    b = app.backend
    name = app.pack.profile.name

    total_rooms = b.query_one("SELECT COUNT(*) c FROM rooms WHERE status='active'")["c"]
    in_house = b.query_one(
        "SELECT COUNT(*) c FROM bookings WHERE status='checked_in'")["c"]
    arrivals = b.query(
        "SELECT b.id, g.name FROM bookings b JOIN guests g ON g.id=b.guest_id "
        "WHERE b.status='confirmed' AND b.check_in=?", [today])
    departures = b.query(
        "SELECT b.id, g.name FROM bookings b JOIN guests g ON g.id=b.guest_id "
        "WHERE b.status='checked_in' AND b.check_out=?", [today])
    revenue = b.query_one(
        "SELECT COALESCE(SUM(total_pkr),0) s FROM bookings "
        "WHERE status IN ('confirmed','checked_in') AND check_in<=? AND check_out>?",
        [today, today])["s"]
    open_tasks = b.query(
        "SELECT kind, detail FROM staff_tasks WHERE status='open' ORDER BY created_at LIMIT 10")
    anomalies = app.ledger.anomalies(limit=20)
    new_bookings = b.query_one(
        "SELECT COUNT(*) c FROM bookings WHERE date(created_at)=date(?)", [today])["c"]

    occ = (in_house / total_rooms * 100) if total_rooms else 0
    lines = [
        f"📊 *{name} — Daily Digest ({today})*",
        f"🛏️ Occupancy: {in_house}/{total_rooms} ({occ:.0f}%)",
        f"💰 Revenue on books (active stays): Rs {revenue:,}",
        f"📥 New bookings today: {new_bookings}",
        f"🛬 Arrivals today ({len(arrivals)}): " + (", ".join(a["name"] for a in arrivals) or "—"),
        f"🛫 Departures today ({len(departures)}): " + (", ".join(d["name"] for d in departures) or "—"),
        f"🧹 Open staff tasks ({len(open_tasks)}):",
    ]
    for t in open_tasks[:5]:
        lines.append(f"   • [{t['kind']}] {t['detail'][:60]}")
    if not open_tasks:
        lines.append("   • none 🎉")
    if anomalies:
        lines.append(f"⚠️ Ledger anomalies ({len(anomalies)}):")
        for a in anomalies[:5]:
            lines.append(f"   • {a['kind']}: {a['action']} ({a['ts'][:10]})")
    else:
        lines.append("✅ No ledger anomalies.")
    return "\n".join(lines)


def send_digest(app, today: str | None = None) -> str:
    text = build_digest(app, today)
    owner = app.pack.profile.owner_phone
    if owner:
        msg_id = app.transport.send(OutboundMessage(to=owner, text=text))
        app.ledger.record(
            actor="agent:innkeeper", action="digest.send", tier=EscalationTier.AUTO,
            before={}, after={"to": owner, "msg_id": msg_id},
            undo={"action": "message.send", "msg_id": msg_id},
            note=f"daily digest {today or date.today().isoformat()}",
        )
    return text
