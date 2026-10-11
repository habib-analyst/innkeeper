"""Hostel skill pack: booking flow, front desk, housekeeping, reviews, digest.

This is the day-one vertical. Restaurant/clinic packs follow the same shape.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import uuid

from innkeeper.agent import brain
from innkeeper.core.escalation import EscalationTier, hostel_policy
from innkeeper.core.ledger import TransactionLedger
from innkeeper.skills.base import BusinessProfile, SkillPack
from innkeeper.transport.base import OutboundMessage


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:8]


def _send(app, to: str, text: str, action: str = "message.send", tier=None, journal: bool = True):
    tier = tier or app.policy.tier_for(action, {})
    msg_id = app.transport.send(OutboundMessage(to=to, text=text))
    if journal:
        app.ledger.record(
            actor="agent:innkeeper", action=action, tier=tier,
            before={}, after={"to": to, "text": text[:200], "msg_id": msg_id},
            undo={"action": "message.send", "msg_id": msg_id},
            note=f"sent to {to}",
        )
    return msg_id


def _escalation_context(app, sender: str, slots: dict) -> dict:
    return {
        "angry_guest": bool(slots.get("angry_guest")),
        "high_value_threshold_pkr": app.pack.profile.high_value_threshold_pkr,
        "amount_pkr": slots.get("amount_pkr", 0),
    }


def _propose_or_act(app, action: str, ctx: dict, do_fn, sender: str, proposal_text: str):
    """Enforce the escalation tier: AUTO acts, CONFIRM asks the owner, HUMAN_ONLY drafts."""
    tier = app.policy.tier_for(action, ctx)
    if tier == EscalationTier.AUTO:
        return do_fn()
    if tier == EscalationTier.CONFIRM:
        prop_id = _new_id("prop")
        app.pending_confirmations[prop_id] = {"action": action, "do_fn": do_fn, "sender": sender}
        app.ledger.record(
            actor="agent:innkeeper", action=action, tier=tier,
            before={}, after={"proposal_id": prop_id, "for": sender},
            undo={"action": "proposal.withdraw", "proposal_id": prop_id},
            note=proposal_text, status="proposed",
        )
        _send(app, app.pack.profile.owner_phone,
              f"⚠️ Approval needed ({prop_id}): {proposal_text}\nReply APPROVE {prop_id} or REJECT {prop_id}",
              action="message.send", journal=True)
        _send(app, sender,
              "I've prepared that for you — just confirming with the manager, one moment please. 🙏",
              action="message.send", journal=False)
        return {"escalated": "confirm", "proposal_id": prop_id}
    # HUMAN_ONLY
    app.ledger.record(
        actor="agent:innkeeper", action=action, tier=tier,
        before={}, after={"for": sender}, undo={"action": "noop"},
        note=f"HUMAN ONLY — drafted for owner: {proposal_text}", status="proposed",
    )
    _send(app, app.pack.profile.owner_phone,
          f"🧑‍💼 Needs you personally: {proposal_text} (guest {sender})",
          action="message.send", journal=True)
    _send(app, sender,
          "Let me get the manager for this personally — they'll reply here shortly. 🙏",
          action="message.send", journal=False)
    return {"escalated": "human_only"}


# ---------------------------------------------------------------------------
# intent handlers
# ---------------------------------------------------------------------------

def handle_greeting(app, sender, slots, session):
    _send(app, sender,
          f"Assalam-o-Alaikum! Welcome to {app.pack.profile.name} 🏠\n"
          "I can check availability, book a room, or help with your stay.\n"
          "Just tell me your dates — e.g. \"room for 2, tomorrow to Friday\".")


def handle_availability(app, sender, slots, session):
    check_in = slots.get("check_in") or session.slots.get("check_in")
    session.slots.update({k: v for k, v in slots.items() if v is not None})
    if not check_in:
        session.state = "booking_dates"
        session.slots.update(slots)
        _send(app, sender, "Sure! What dates do you need the room for? (e.g. \"12 Oct to 14 Oct\")")
        return
    check_out = slots.get("check_out")
    if not check_out:
        session.state = "booking_dates"
        session.slots.update(slots)
        _send(app, sender, f"Got it — checking in {check_in}. And checking out when?")
        return
    room_type = slots.get("room_type")
    rooms = app.backend.rooms_available(check_in, check_out, room_type)
    if not rooms:
        _send(app, sender,
              f"I'm sorry — we're fully booked {check_in} → {check_out}"
              + (f" for {room_type} rooms" if room_type else "") + ". 🙏\n"
              "Want me to check nearby dates?")
        session.state = "idle"
        return
    lines = [f"✅ Available {check_in} → {check_out}:"]
    for r in rooms[:6]:
        lines.append(f"• {r['name']} ({r['room_type']}, {r['capacity']} guests) — Rs {r['base_price_pkr']}/night")
    lines.append("\nReply \"book\" + room name to confirm, e.g. \"book Deluxe 1\".")
    session.state = "booking_confirm"
    session.slots.update({"check_in": check_in, "check_out": check_out,
                          "room_type": room_type, "options": [dict(r) for r in rooms]})
    _send(app, sender, "\n".join(lines))


def handle_book(app, sender, slots, session):
    # guest names a room (or we pick cheapest available)
    text_room = (slots.get("raw_text") or "").lower()
    options = session.slots.get("options") or []
    room = None
    for r in options:
        if r["name"].lower() in text_room or r["id"].lower() in text_room:
            room = r
            break
    if room is None and options:
        room = min(options, key=lambda r: r["base_price_pkr"])
    if room is None:
        # no prior availability check — run it first
        return handle_availability(app, sender, slots, session)

    check_in = session.slots.get("check_in") or slots.get("check_in")
    check_out = session.slots.get("check_out") or slots.get("check_out")
    guests = slots.get("guests") or session.slots.get("guests") or 1
    if not (check_in and check_out):
        session.state = "booking_dates"
        _send(app, sender, "Almost there — what are your check-in and check-out dates?")
        return

    # re-verify availability (no double-booking between quote and confirm)
    still = app.backend.rooms_available(check_in, check_out)
    if not any(r["id"] == room["id"] for r in still):
        _send(app, sender, "Ah — that room just got taken for those dates. Let me show you what's left:")
        session.slots["options"] = [dict(r) for r in still]
        return handle_availability(app, sender, {"check_in": check_in, "check_out": check_out}, session)

    nights = (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days
    total = nights * room["base_price_pkr"]
    ctx = _escalation_context(app, sender, {**slots, "amount_pkr": total})
    ctx_tier = app.policy.tier_for("booking.create", ctx)

    def do_book():
        guest = app.backend.query_one("SELECT * FROM guests WHERE phone=?", [sender])
        if guest is None:
            guest_id = _new_id("g")
            app.backend.insert("guests", {"id": guest_id, "name": slots.get("guest_name", "Guest"),
                                         "phone": sender, "notes": "", "total_stays": 0,
                                         "created_at": _now()})
            app.ledger.record(actor="agent:innkeeper", action="guest.upsert",
                              tier=EscalationTier.AUTO, before={}, after={"guest_id": guest_id},
                              undo={"action": "guest.upsert", "guest_id": guest_id, "created": True})
        else:
            guest_id = guest["id"]
        booking_id = _new_id("b")
        before = {}
        app.backend.insert("bookings", {
            "id": booking_id, "guest_id": guest_id, "room_id": room["id"],
            "check_in": check_in, "check_out": check_out, "guests_count": guests,
            "total_pkr": total, "status": "confirmed", "source": "whatsapp",
            "created_at": _now(),
        })
        app.ledger.record(
            actor="agent:innkeeper", action="booking.create", tier=ctx_tier,
            before=before, after={"booking_id": booking_id, "room": room["name"], "total_pkr": total},
            undo={"action": "booking.create", "booking_id": booking_id},
            note=f"booking {booking_id} for {sender}",
        )
        # housekeeping prep task for the room
        task_id = _new_id("t")
        app.backend.insert("staff_tasks", {
            "id": task_id, "kind": "checkin_prep", "room_id": room["id"],
            "booking_id": booking_id, "detail": f"Prepare {room['name']} for {check_in} ({guests} guests)",
            "assignee": "", "status": "open", "due_at": check_in, "created_at": _now(),
        })
        app.ledger.record(actor="agent:innkeeper", action="task.create", tier=EscalationTier.AUTO,
                          before={}, after={"task_id": task_id},
                          undo={"action": "task.create", "task_id": task_id})
        if app.pack.profile.staff_group:
            _send(app, app.pack.profile.staff_group,
                  f"🧹 New booking {booking_id}: prepare {room['name']} for {check_in} ({guests} guests).",
                  action="message.send", journal=True)
        pay_lines = "\n".join(f"• {m}: {d}" for m, d in app.pack.profile.payment_methods.items())
        _send(app, sender,
              f"🎉 Booked! {room['name']} ({room['room_type']}) — {check_in} → {check_out}\n"
              f"{nights} night(s), {guests} guest(s). Total: Rs {total:,}\n"
              f"Booking ID: {booking_id}\n\n"
              f"Please send advance to confirm:\n{pay_lines}\n\n"
              f"Check-in {app.pack.profile.checkin_time}, check-out {app.pack.profile.checkout_time}. "
              "Share your payment screenshot here. See you soon! 🙏")
        session.state = "idle"
        session.booking_id = booking_id
        session.slots.clear()
        return {"booking_id": booking_id, "total_pkr": total}

    return _propose_or_act(app, "booking.create", ctx, do_book, sender,
                           f"booking {room['name']} {check_in}→{check_out}, Rs {total:,} for {sender}")


def handle_cancel(app, sender, slots, session):
    b = _active_booking(app, sender)
    if not b:
        _send(app, sender, "I couldn't find an active booking on this number. Share your booking ID?")
        return

    def do_cancel():
        app.backend.update("bookings", "id=?", [b["id"]], {"status": "cancelled"})
        app.ledger.record(actor="agent:innkeeper", action="booking.cancel",
                          tier=EscalationTier.CONFIRM, before={"status": b["status"]},
                          after={"status": "cancelled"},
                          undo={"action": "booking.cancel", "booking_id": b["id"], "prev_status": b["status"]},
                          note=f"cancelled {b['id']}")
        _send(app, sender, f"Done — booking {b['id']} is cancelled. No charge. Hope to host you another time! 🙏")
        return {"cancelled": b["id"]}

    return _propose_or_act(app, "booking.cancel", _escalation_context(app, sender, slots),
                           do_cancel, sender, f"cancel booking {b['id']}")


def handle_modify(app, sender, slots, session):
    b = _active_booking(app, sender)
    if not b:
        _send(app, sender, "I couldn't find an active booking on this number. Share your booking ID?")
        return
    new_in = slots.get("check_in")
    new_out = slots.get("check_out")
    if not (new_in and new_out):
        _send(app, sender, "What new dates would you like? (e.g. \"shift to 15 Oct – 17 Oct\")")
        session.state = "modify_dates"
        return

    def do_modify():
        prev = {"check_in": b["check_in"], "check_out": b["check_out"], "total_pkr": b["total_pkr"]}
        room = app.backend.query_one("SELECT * FROM rooms WHERE id=?", [b["room_id"]])
        nights = (date.fromisoformat(new_out) - date.fromisoformat(new_in)).days
        total = nights * room["base_price_pkr"]
        app.backend.update("bookings", "id=?", [b["id"]],
                           {"check_in": new_in, "check_out": new_out, "total_pkr": total})
        app.ledger.record(actor="agent:innkeeper", action="booking.modify",
                          tier=EscalationTier.CONFIRM, before=prev,
                          after={"check_in": new_in, "check_out": new_out, "total_pkr": total},
                          undo={"action": "booking.modify", "booking_id": b["id"], "prev": prev},
                          note=f"modified {b['id']}")
        _send(app, sender, f"Updated ✅ — {b['id']} is now {new_in} → {new_out}. New total: Rs {total:,}.")
        return {"modified": b["id"]}

    return _propose_or_act(app, "booking.modify", _escalation_context(app, sender, slots),
                           do_modify, sender, f"modify booking {b['id']} to {new_in}→{new_out}")


def handle_checkin(app, sender, slots, session):
    b = _active_booking(app, sender)
    target = b["id"] if b else (slots.get("booking_id") or "walk-in")
    app.backend.update("bookings", "id=?", [b["id"]], {"status": "checked_in"}) if b else None
    if b:
        app.ledger.record(actor="agent:innkeeper", action="booking.checkin", tier=EscalationTier.AUTO,
                          before={"status": "confirmed"}, after={"status": "checked_in"},
                          undo={"action": "booking.checkin", "booking_id": b["id"]})
    _send(app, sender,
          f"Welcome! 🎉 Your room is ready.\nWiFi: {app.pack.profile.name}-Guest / stay2026\n"
          f"Checkout is {app.pack.profile.checkout_time}. Need anything — towels, extra bed, food — just message here.")


def handle_checkout(app, sender, slots, session):
    b = _active_booking(app, sender, any_status=("checked_in", "confirmed"))
    if b:
        app.backend.update("bookings", "id=?", [b["id"]], {"status": "checked_out"})
        app.ledger.record(actor="agent:innkeeper", action="booking.checkout", tier=EscalationTier.AUTO,
                          before={"status": b["status"]}, after={"status": "checked_out"},
                          undo={"action": "booking.checkout", "booking_id": b["id"]})
        room = app.backend.query_one("SELECT * FROM rooms WHERE id=?", [b["room_id"]])
        task_id = _new_id("t")
        app.backend.insert("staff_tasks", {
            "id": task_id, "kind": "housekeeping", "room_id": b["room_id"], "booking_id": b["id"],
            "detail": f"Full clean of {room['name']} after checkout", "assignee": "",
            "status": "open", "due_at": date.today().isoformat(), "created_at": _now()})
        app.ledger.record(actor="agent:innkeeper", action="task.create", tier=EscalationTier.AUTO,
                          before={}, after={"task_id": task_id},
                          undo={"action": "task.create", "task_id": task_id})
        if app.pack.profile.staff_group:
            _send(app, app.pack.profile.staff_group, f"🧹 Checkout done — full clean needed: {room['name']}.",
                  action="message.send", journal=True)
    _send(app, sender,
          "Thanks for staying with us! 🙏\n"
          "If you had a good stay, a Google review means the world to small guest houses like ours:\n"
          f"{app.pack.profile.review_link}\nSafe travels!")
    # schedule the review nudge via the reviews module
    if b:
        from innkeeper.agent import reviews as reviews_mod
        reviews_mod.schedule_request(app, b["id"], sender)


def handle_late_checkout(app, sender, slots, session):
    fee = app.pack.profile.late_checkout_fee_pkr
    _send(app, sender,
          f"No problem — late checkout (till 4 PM) is Rs {fee:,} extra. "
          "Shall I add it to your booking? Reply \"yes\" to confirm.")
    session.state = "late_checkout_confirm"


def handle_housekeeping(app, sender, slots, session):
    detail = slots.get("raw_text", "housekeeping request")
    b = _active_booking(app, sender)
    task_id = _new_id("t")
    app.backend.insert("staff_tasks", {
        "id": task_id, "kind": "housekeeping",
        "room_id": b["room_id"] if b else None,
        "booking_id": b["id"] if b else None,
        "detail": detail[:200], "assignee": "", "status": "open",
        "due_at": "", "created_at": _now()})
    app.ledger.record(actor="agent:innkeeper", action="task.create", tier=EscalationTier.AUTO,
                      before={}, after={"task_id": task_id, "detail": detail[:200]},
                      undo={"action": "task.create", "task_id": task_id})
    if app.pack.profile.staff_group:
        _send(app, app.pack.profile.staff_group, f"🧹 Guest request ({sender}): {detail[:200]}",
              action="message.send", journal=True)
    _send(app, sender, "On it! 🧹 I've sent your request to our housekeeping team — they'll be with you shortly.")


def handle_payment_info(app, sender, slots, session):
    pay_lines = "\n".join(f"• {m}: {d}" for m, d in app.pack.profile.payment_methods.items())
    _send(app, sender, f"You can pay via:\n{pay_lines}\n\nJust share the screenshot/transaction ID here after paying. 🙏")


def handle_complaint(app, sender, slots, session):
    # Complaints always escalate: the agent empathizes immediately (AUTO),
    # then drafts a briefing for the owner to resolve personally (HUMAN_ONLY).
    _send(app, sender,
          "I'm really sorry to hear that. 🙏 You're right to flag it — "
          "I've sent this straight to the manager, who will reply here personally very shortly.")
    return _propose_or_act(
        app, "complaint.brief", {"angry_guest": True},
        lambda: {"briefed": True}, sender,
        f"guest complaint from {sender}: {(slots.get('raw_text') or '')[:200]}")


def handle_review(app, sender, slots, session):
    _send(app, sender,
          "Thank you! 🌟 Here's our Google review link:\n"
          f"{app.pack.profile.review_link}\nIt takes 30 seconds and helps us enormously.")


def handle_unknown(app, sender, slots, session):
    if session.state == "booking_dates":
        return handle_availability(app, sender, slots, session)
    if session.state == "late_checkout_confirm" and "yes" in (slots.get("raw_text") or "").lower():
        _send(app, sender, "Done ✅ — late checkout added. Enjoy the extra hours!")
        session.state = "idle"
        return
    _send(app, sender,
          "I'm not sure I caught that — I can check availability, book a room, help with "
          "check-in/out, or pass a message to our team. What do you need? 🙂")


def handle_owner_approve(app, sender, slots, session):
    """Owner taps: APPROVE <prop_id> / REJECT <prop_id>."""
    raw = (slots.get("raw_text") or "").upper()
    m = __import__("re").search(r"(APPROVE|REJECT)\s+(PROP[A-Z0-9]+)", raw)
    if not m:
        return False
    verdict, prop_id = m.group(1), m.group(2).lower()
    prop = app.pending_confirmations.pop(prop_id, None)
    if not prop:
        _send(app, sender, f"I couldn't find pending request {prop_id} — it may have expired.")
        return True
    if verdict == "APPROVE":
        result = prop["do_fn"]()
        app.ledger.record(actor=f"human:owner", action=f"{prop['action']}.approved",
                          tier=EscalationTier.CONFIRM, before={}, after={"proposal_id": prop_id},
                          undo={"action": "noop"}, note=f"owner approved {prop_id}")
        _send(app, prop["sender"], "Good news — the manager approved it! ✅")
    else:
        app.ledger.record(actor="human:owner", action=f"{prop['action']}.rejected",
                          tier=EscalationTier.CONFIRM, before={}, after={"proposal_id": prop_id},
                          undo={"action": "noop"}, note=f"owner rejected {prop_id}", status="rejected")
        _send(app, prop["sender"], "The manager reviewed your request and we can't do that one, sorry. "
                                  "Is there something else I can help with? 🙏")
        _send(app, sender, f"Noted — {prop_id} rejected, guest informed.")
    return True


def _active_booking(app, sender, any_status=("confirmed", "checked_in")):
    placeholders = ",".join("?" for _ in any_status)
    return app.backend.query_one(
        f"""SELECT b.* FROM bookings b JOIN guests g ON g.id=b.guest_id
            WHERE g.phone=? AND b.status IN ({placeholders})
            ORDER BY b.check_in DESC LIMIT 1""",
        [sender, *any_status])


def make_hostel_pack(profile: BusinessProfile) -> SkillPack:
    handlers = {
        "greeting": handle_greeting,
        "availability": handle_availability,
        "book": handle_book,
        "cancel_booking": handle_cancel,
        "modify_booking": handle_modify,
        "checkin": handle_checkin,
        "checkout": handle_checkout,
        "late_checkout": handle_late_checkout,
        "housekeeping": handle_housekeeping,
        "payment_info": handle_payment_info,
        "complaint": handle_complaint,
        "review": handle_review,
        "unknown": handle_unknown,
    }
    return SkillPack(name="hostel", profile=profile, policy=hostel_policy(), handlers=handlers)
