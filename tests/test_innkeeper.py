"""Tests for innkeeper — the AI innkeeper. Run: python -m pytest tests/ -q"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from innkeeper.agent import brain
from innkeeper.app import InnkeeperApp
from innkeeper.core.backend import generate_backend
from innkeeper.core.escalation import EscalationTier, hostel_policy
from innkeeper.core.ledger import TransactionLedger
from innkeeper.skills.base import BusinessProfile
from innkeeper.skills.hostel.pack import make_hostel_pack
from innkeeper.transport.mock import MockTransport

OWNER = "+929990000000"

ROOMS = [
    {"id": "r1", "name": "Deluxe 1", "room_type": "deluxe", "capacity": 2, "base_price_pkr": 8500},
    {"id": "r2", "name": "Standard 1", "room_type": "double", "capacity": 2, "base_price_pkr": 5500},
]


def make_app(tmp_path, name="t"):
    backend = generate_backend(str(tmp_path / f"{name}.db"), "Test House", vertical="hostel")
    for r in ROOMS:
        backend.insert("rooms", {**r, "status": "active"})
    profile = BusinessProfile(
        name="Test House", owner_phone=OWNER, staff_group="+929990000001",
        payment_methods={"Easypaisa": "0345-0000000"},
        review_link="https://g.page/test/review",
    )
    return InnkeeperApp(backend, make_hostel_pack(profile), MockTransport())


# --- brain -----------------------------------------------------------------

def test_classify_booking_intent():
    intent, slots = brain.classify("book Deluxe 1")
    assert intent == "book"


def test_classify_availability_mid_sentence():
    intent, slots = brain.classify("I want to book a room tomorrow")
    assert intent == "availability"
    assert "check_in" in slots


def test_classify_cancel():
    intent, _ = brain.classify("please cancel my booking")
    assert intent == "cancel_booking"


def test_classify_complaint_and_angry_flag():
    intent, slots = brain.classify("This is terrible!!! I want a refund!!!")
    assert intent == "complaint"
    assert slots.get("angry_guest") is True


def test_extract_dates_relative():
    from datetime import date
    ds = brain.extract_dates("room tomorrow to day after tomorrow", today=date(2026, 11, 10))
    assert ds[0] == "2026-11-11" and ds[1] == "2026-11-12"


def test_extract_guests():
    assert brain.extract_guests("room for 3 guests") == 3
    assert brain.extract_guests("double room please") == 2


# --- backend ---------------------------------------------------------------

def test_backend_generate_and_availability(tmp_path):
    app = make_app(tmp_path, "b1")
    avail = app.backend.rooms_available("2026-11-11", "2026-11-13")
    assert len(avail) == 2
    app.close()


def test_backend_refuses_overwrite(tmp_path):
    make_app(tmp_path, "b2").close()
    import pytest as _p
    with _p.raises(FileExistsError):
        generate_backend(str(tmp_path / "b2.db"), "x", vertical="hostel")


def test_export_json(tmp_path):
    app = make_app(tmp_path, "b3")
    data = app.backend.export_json()
    assert data["business"] == "Test House"
    assert len(data["tables"]["rooms"]) == 2
    app.close()


# --- booking flow ----------------------------------------------------------

def test_happy_path_booking(tmp_path):
    app = make_app(tmp_path, "h1")
    t = app.transport
    g = "+923001234567"
    t.guest_says(g, "Salam, room for 2 from 2026-11-11 to 2026-11-13?")
    app.pump()
    assert "Available" in t.last_to(g)
    t.guest_says(g, "book Deluxe 1")
    app.pump()
    reply = t.last_to(g)
    assert "Booked!" in reply and "Rs 17,000" in reply
    b = app.backend.query_one("SELECT * FROM bookings")
    assert b["status"] == "confirmed" and b["total_pkr"] == 17000
    # journaled as reversible AUTO transaction
    entries = app.ledger.list(action="booking.create")
    assert len(entries) == 1 and entries[0].tier == "auto" and entries[0].status == "done"
    app.close()


def test_no_double_booking(tmp_path):
    app = make_app(tmp_path, "h2")
    t = app.transport
    for g in ("+923001111111", "+923002222222"):
        t.guest_says(g, "room from 2026-11-11 to 2026-11-13?")
        app.pump()
    t.guest_says("+923001111111", "book Deluxe 1")
    app.pump()
    t.guest_says("+923002222222", "book Deluxe 1")
    app.pump()
    n = app.backend.query_one(
        "SELECT COUNT(*) c FROM bookings WHERE room_id='r1' AND status IN ('confirmed','checked_in')")["c"]
    assert n == 1
    app.close()


def test_cancel_requires_owner_approval(tmp_path):
    app = make_app(tmp_path, "h3")
    t = app.transport
    g = "+923001234567"
    t.guest_says(g, "room from 2026-11-11 to 2026-11-13?")
    app.pump()
    t.guest_says(g, "book Deluxe 1")
    app.pump()
    t.guest_says(g, "cancel my booking please")
    app.pump()
    # guest asked for approval, not cancelled yet
    b = app.backend.query_one("SELECT * FROM bookings")
    assert b["status"] == "confirmed"
    assert "confirming with the manager" in t.last_to(g)
    # owner approves
    import re
    prop = re.search(r"Approval needed \((prop[0-9a-f]+)\)", t.last_to(OWNER)).group(1)
    t.guest_says(OWNER, f"APPROVE {prop}")
    app.pump()
    b = app.backend.query_one("SELECT * FROM bookings")
    assert b["status"] == "cancelled"
    app.close()


def test_high_value_booking_escalates(tmp_path):
    app = make_app(tmp_path, "h4")
    t = app.transport
    g = "+923001234567"
    t.guest_says(g, "deluxe room for 15 nights from 2026-11-11 to 2026-11-26")
    app.pump()
    t.guest_says(g, "book Deluxe 1")
    app.pump()
    entries = app.ledger.list(action="booking.create", status="proposed")
    assert len(entries) == 1 and entries[0].tier == "confirm"
    app.close()


def test_complaint_is_human_only(tmp_path):
    app = make_app(tmp_path, "h5")
    t = app.transport
    t.guest_says("+923009999999", "This is awful!!! worst service, I want the manager now!!!")
    app.pump()
    entries = app.ledger.list(action="complaint.brief")
    assert len(entries) == 1 and entries[0].tier == "human_only"
    assert any(m.to == OWNER for m in t.sent)
    app.close()


# --- ledger reversibility ----------------------------------------------------

def test_ledger_revert_booking(tmp_path):
    app = make_app(tmp_path, "h6")
    t = app.transport
    g = "+923001234567"
    t.guest_says(g, "room from 2026-11-11 to 2026-11-13?")
    app.pump()
    t.guest_says(g, "book Deluxe 1")
    app.pump()
    e = app.ledger.list(action="booking.create", status="done")[0]
    app.ledger.revert(e.id, actor="human:owner")
    b = app.backend.query_one("SELECT * FROM bookings")
    assert b["status"] == "cancelled"
    assert app.ledger.get(e.id).status == "reverted"
    app.close()


def test_ledger_revert_task_deletes(tmp_path):
    app = make_app(tmp_path, "h7")
    t = app.transport
    t.guest_says("+923001234567", "need towels, please clean")
    app.pump()
    e = app.ledger.list(action="task.create", status="done")[0]
    tid = e.after["task_id"]
    app.ledger.revert(e.id, actor="human:owner")
    assert app.backend.query_one("SELECT 1 FROM staff_tasks WHERE id=?", [tid]) is None
    app.close()


def test_policy_fail_closed_on_unknown_action():
    policy = hostel_policy()
    assert policy.tier_for("nuclear.launch", {}) == EscalationTier.CONFIRM


# --- reviews & digest --------------------------------------------------------

def test_checkout_schedules_review_and_digest(tmp_path):
    app = make_app(tmp_path, "h8")
    t = app.transport
    g = "+923001234567"
    t.guest_says(g, "room from 2026-11-11 to 2026-11-13?")
    app.pump()
    t.guest_says(g, "book Deluxe 1")
    app.pump()
    t.guest_says(g, "arrived, checking in")
    app.pump()
    t.guest_says(g, "checking out now")
    app.pump()
    assert "google" in t.last_to(g).lower() or "review" in t.last_to(g).lower()
    from innkeeper.agent import digest as digest_mod
    text = digest_mod.build_digest(app, "2026-11-11")
    assert "Daily Digest" in text and "Occupancy" in text
    app.close()


def test_scheduler_idempotent(tmp_path):
    app = make_app(tmp_path, "h9")
    r1 = app.run_scheduler(today="2026-11-11")
    r2 = app.run_scheduler(today="2026-11-11")
    assert r1 and not r2  # second run same day fires nothing
    app.close()


# --- transport ---------------------------------------------------------------

def test_whatsapp_cloud_requires_creds():
    from innkeeper.transport.whatsapp_cloud import WhatsAppCloudTransport
    import pytest as _p
    with _p.raises(RuntimeError):
        WhatsAppCloudTransport(token="", phone_number_id="")


def test_whatsapp_webhook_ingest():
    from innkeeper.transport.whatsapp_cloud import WhatsAppCloudTransport
    tr = WhatsAppCloudTransport.__new__(WhatsAppCloudTransport)  # bypass creds check
    msgs = tr.ingest_webhook({"entry": [{"changes": [{"value": {"messages": [
        {"from": "923001234567", "id": "wamid.1", "timestamp": "123",
         "type": "text", "text": {"body": "Salam"}}]}}]}]})
    assert len(msgs) == 1 and msgs[0].sender == "923001234567" and msgs[0].text == "Salam"
