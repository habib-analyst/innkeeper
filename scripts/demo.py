"""30-second demo: a full guest journey over simulated WhatsApp.

`innkeeper demo --db oriole-luxury.db` — watch a guest inquire, get availability,
book, check in, request housekeeping, check out, and get the review nudge.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from innkeeper.app import InnkeeperApp
from innkeeper.core.backend import open_backend
from innkeeper.skills.base import BusinessProfile
from innkeeper.skills.hostel.pack import make_hostel_pack
from innkeeper.transport.mock import MockTransport


def build_app(db_path: str):
    backend = open_backend(db_path)
    prof_path = Path(db_path.replace(".db", ".profile.json"))
    prof = json.loads(prof_path.read_text()) if prof_path.exists() else {}
    profile = BusinessProfile(
        name=prof.get("name", "Demo Guest House"),
        owner_phone=prof.get("owner_phone", "+920000000000"),
        staff_group="+920000000001",
        payment_methods=prof.get("payment_methods", {"Easypaisa": "0345-0000000"}),
        review_link=prof.get("review_link", "https://g.page/demo/review"),
    )
    return InnkeeperApp(backend, make_hostel_pack(profile), MockTransport())


SCRIPT = [
    ("guest", "+923001234567", "Salam! Do you have a room for 2 people, tomorrow to day after tomorrow?"),
    ("guest", "+923001234567", "book Deluxe 1"),
    ("guest", "+923001234567", "How do I pay?"),
    ("guest", "+923001234567", "I've arrived, checking in"),
    ("guest", "+923001234567", "Can I get extra towels please, need cleaning"),
    ("guest", "+923001234567", "checking out now, thanks!"),
]


def run_demo(db_path: str, script=None, quiet: bool = False) -> list:
    app = build_app(db_path)
    t = app.transport
    transcript = []
    for who, sender, text in (script or SCRIPT):
        t.guest_says(sender, text)
        app.pump()
        reply = t.last_to(sender)
        transcript.append((sender, text, reply))
        if not quiet:
            print(f"🧑 {sender}: {text}")
            print(f"🏠 innkeeper: {reply}\n")
    # morning after: scheduler fires review request + owner digest
    sched = app.run_scheduler(today="2099-01-01")  # far-future date keeps demo idempotent
    if not quiet:
        print("— next morning: scheduler —")
        for job, msgs in sched.items():
            for m in msgs:
                print(f"⏰ [{job}]: {m[:120]}...")
    app.close()
    return transcript


if __name__ == "__main__":
    import sys
    run_demo(sys.argv[1] if len(sys.argv) > 1 else "demo.db")
