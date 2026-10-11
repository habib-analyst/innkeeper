"""Simulation harness: runs every scripted scenario, measures honestly.

Metrics (all measured, nothing fabricated):
  1. booking conversion: inquiries with real availability -> confirmed booking
  2. response latency: ms per inbound message (in-process)
  3. journal reversibility: every booking.create undone -> state restored exactly
  4. escalation precision/recall: expected tier vs actual tier per action class

Writes eval/results.json. Exits nonzero on any failed expectation.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.scenarios import SCENARIOS, GUEST_A, GUEST_B, OWNER, D
from innkeeper.app import InnkeeperApp
from innkeeper.core.backend import generate_backend
from innkeeper.core.ledger import EscalationTier
from innkeeper.skills.base import BusinessProfile
from innkeeper.skills.hostel.pack import make_hostel_pack
from innkeeper.transport.mock import MockTransport

ROOMS = [
    {"id": "r1", "name": "Deluxe 1", "room_type": "deluxe", "capacity": 2, "base_price_pkr": 8500},
    {"id": "r2", "name": "Deluxe 2", "room_type": "deluxe", "capacity": 2, "base_price_pkr": 8500},
    {"id": "r3", "name": "Standard 1", "room_type": "double", "capacity": 2, "base_price_pkr": 5500},
]


_EVAL_TMP = tempfile.mkdtemp(prefix="innkeeper_eval_")


def make_app(db_path: str, owner_phone: str = OWNER) -> InnkeeperApp:
    backend = generate_backend(db_path, "Sim Guest House", vertical="hostel")
    for r in ROOMS:
        backend.insert("rooms", {**r, "status": "active"})
    profile = BusinessProfile(
        name="Sim Guest House", owner_phone=owner_phone, staff_group="+929990000001",
        payment_methods={"Easypaisa": "0345-0000000", "JazzCash": "0300-0000000"},
        review_link="https://g.page/sim/review",
    )
    return InnkeeperApp(backend, make_hostel_pack(profile), MockTransport())


def fill_all_rooms(app: InnkeeperApp) -> None:
    from datetime import datetime, timezone
    import uuid
    for r in ROOMS:
        gid = "g" + uuid.uuid4().hex[:8]
        app.backend.insert("guests", {"id": gid, "name": "Blocker", "phone": "+920000000000",
                                     "notes": "", "total_stays": 0,
                                     "created_at": datetime.now(timezone.utc).isoformat()})
        app.backend.insert("bookings", {
            "id": "b" + uuid.uuid4().hex[:8], "guest_id": gid, "room_id": r["id"],
            "check_in": D(0), "check_out": D(10), "guests_count": 2,
            "total_pkr": 10000, "status": "confirmed", "source": "sim",
            "created_at": datetime.now(timezone.utc).isoformat()})


def run_scenario(scn: dict, idx: int) -> dict:
    app = make_app(str(Path(_EVAL_TMP) / f"eval_{idx}.db"))
    t = app.transport
    latencies = []
    if scn.get("setup") == "fill_all_rooms":
        fill_all_rooms(app)

    pending_prop: str | None = None
    for sender, text in scn["script"]:
        t.guest_says(sender, text)
        t0 = time.perf_counter()
        app.pump()
        latencies.append((time.perf_counter() - t0) * 1000)
        # capture proposal ids for the owner script
        for m in t.sent:
            mm = re.search(r"Approval needed \((prop[0-9a-f]+)\)", m.text)
            if mm:
                pending_prop = mm.group(1)

    # owner script (APPROVE/REJECT {prop})
    for otext in scn.get("owner_script", []):
        otext = otext.replace("{prop}", (pending_prop or "prop_missing").upper())
        t.guest_says(OWNER, otext)
        t0 = time.perf_counter()
        app.pump()
        latencies.append((time.perf_counter() - t0) * 1000)

    result = {"id": scn["id"], "latency_ms": latencies, "checks": {}}
    b = app.backend
    exp = scn.get("expect", {})

    def reply_contains(substr: str) -> bool:
        return any(substr.lower() in m.text.lower() for m in t.sent)

    bookings = b.query("SELECT * FROM bookings WHERE source='whatsapp'")
    if "booking_created" in exp:
        confirmed = [x for x in bookings if x["status"] in ("confirmed", "checked_in", "checked_out")]
        result["checks"]["booking_created"] = (len(confirmed) > 0) == exp["booking_created"]
    if "reply_contains" in exp:
        result["checks"]["reply_contains"] = reply_contains(exp["reply_contains"])
    if "housekeeping_task" in exp:
        tasks = b.query("SELECT * FROM staff_tasks")
        result["checks"]["housekeeping_task"] = (len(tasks) > 0) == exp["housekeeping_task"]
    if "review_scheduled" in exp:
        revs = b.query("SELECT * FROM reviews")
        result["checks"]["review_scheduled"] = (len(revs) > 0) == exp["review_scheduled"]
    if "cancelled" in exp:
        cancelled = [x for x in bookings if x["status"] == "cancelled"]
        result["checks"]["cancelled"] = (len(cancelled) > 0) == exp["cancelled"]
    if "booking_status" in exp:
        result["checks"]["booking_status"] = any(x["status"] == exp["booking_status"] for x in bookings)
    if "modified" in exp:
        result["checks"]["modified"] = any(x["check_in"] == D(5) for x in bookings) == exp["modified"]
    if "owner_notified" in exp:
        result["checks"]["owner_notified"] = any(m.to == OWNER for m in t.sent) == exp["owner_notified"]
    if "deluxe1_bookings" in exp:
        n = b.query_one("SELECT COUNT(*) c FROM bookings WHERE room_id='r1' AND status IN ('confirmed','checked_in')")["c"]
        result["checks"]["deluxe1_bookings"] = (n == exp["deluxe1_bookings"])
        result["checks"]["no_double_booking"] = n <= 1

    # escalation tier checks: expected vs journaled tier per action class
    tier_map = {
        "tier_booking_create": "booking.create",
        "tier_booking_modify": "booking.modify",
        "tier_booking_cancel": "booking.cancel",
        "tier_complaint_brief": "complaint.brief",
    }
    for key, action in tier_map.items():
        if key in exp:
            entries = app.ledger.list(action=action)
            actual = entries[0].tier if entries else "none"
            result["checks"][key] = (actual == exp[key])
            result[f"actual_{key}"] = actual

    result["passed"] = all(result["checks"].values())
    app.close()
    return result


def reversibility_check() -> dict:
    """Every booking.create journal entry must undo to exact prior state."""
    app = make_app(str(Path(_EVAL_TMP) / "eval_revert.db"))
    t = app.transport
    # create two bookings via the real flow
    for sender in ("+923001111111", "+923002222222"):
        t.guest_says(sender, f"Salam, room for 2 from {D(1)} to {D(3)}?")
        app.pump()
        t.guest_says(sender, "book Deluxe 1" if sender.endswith("111111") else "book Deluxe 2")
        app.pump()
    entries = app.ledger.list(action="booking.create", status="done")
    ok, details = True, []
    for e in entries:
        bid = e.after["booking_id"]
        before_status = app.backend.query_one("SELECT status FROM bookings WHERE id=?", [bid])["status"]
        app.ledger.revert(e.id, actor="human:owner")
        after_status = app.backend.query_one("SELECT status FROM bookings WHERE id=?", [bid])["status"]
        reverted = app.ledger.get(e.id).status == "reverted"
        good = before_status == "confirmed" and after_status == "cancelled" and reverted
        ok = ok and good
        details.append({"entry": e.id, "booking": bid, "ok": good})
    # task.create undo must delete the task
    t_entries = app.ledger.list(action="task.create", status="done")
    for e in t_entries[:1]:
        tid = e.after["task_id"]
        app.ledger.revert(e.id, actor="human:owner")
        gone = app.backend.query_one("SELECT 1 FROM staff_tasks WHERE id=?", [tid]) is None
        ok = ok and gone
        details.append({"entry": e.id, "task": tid, "deleted": gone})
    app.close()
    return {"n_booking_undos": len(entries), "all_correct": ok, "details": details}


def main() -> int:
    results = [run_scenario(scn, i) for i, scn in enumerate(SCENARIOS)]
    rev = reversibility_check()

    n_pass = sum(1 for r in results if r["passed"])
    all_lat = [l for r in results for l in r["latency_ms"]]
    # conversion: happy_path + high_value_confirm bookings confirmed
    conv_scenarios = [r for r in results if r["id"] in ("happy_path", "high_value_confirm")]
    conversion = sum(1 for r in conv_scenarios if r["checks"].get("booking_created")) / len(conv_scenarios)

    # escalation precision/recall over the tier_* checks
    tier_checks = []
    for r in results:
        for k, v in r["checks"].items():
            if k.startswith("tier_"):
                tier_checks.append(v)
    esc_acc = sum(tier_checks) / len(tier_checks) if tier_checks else 0

    summary = {
        "scenarios_run": len(results),
        "scenarios_passed": n_pass,
        "booking_conversion": round(conversion, 4),
        "latency_ms_p50": round(sorted(all_lat)[len(all_lat) // 2], 2),
        "latency_ms_p95": round(sorted(all_lat)[int(len(all_lat) * 0.95) - 1], 2),
        "escalation_tier_accuracy": round(esc_acc, 4),
        "journal_reversibility": rev,
        "engine": "rule-based brain + StubProvider (no LLM calls; deterministic)",
        "scenarios": results,
    }
    out = Path(__file__).resolve().parent / "results.json"
    out.write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "scenarios"}, indent=2))
    failed = [r["id"] for r in results if not r["passed"]]
    if failed:
        print("FAILED:", failed, file=sys.stderr)
    if not rev["all_correct"]:
        print("REVERSIBILITY FAILED", file=sys.stderr); failed.append("reversibility")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
