"""innkeeper CLI: init a business backend, run the demo, print the digest."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from innkeeper import generate_backend
from innkeeper.skills.base import BusinessProfile
from innkeeper.skills.hostel.pack import make_hostel_pack


DEFAULT_ROOMS = [
    {"id": "r1", "name": "Deluxe 1", "room_type": "deluxe", "capacity": 2, "base_price_pkr": 8500},
    {"id": "r2", "name": "Deluxe 2", "room_type": "deluxe", "capacity": 2, "base_price_pkr": 8500},
    {"id": "r3", "name": "Standard 1", "room_type": "double", "capacity": 2, "base_price_pkr": 5500},
    {"id": "r4", "name": "Standard 2", "room_type": "double", "capacity": 2, "base_price_pkr": 5500},
    {"id": "r5", "name": "Single 1", "room_type": "single", "capacity": 1, "base_price_pkr": 3500},
]


def cmd_init(args) -> int:
    db_path = Path(args.directory or ".") / f"{args.name}.db"
    profile = BusinessProfile(
        name=args.name.replace("-", " ").title(),
        owner_phone=args.owner_phone or "",
        payment_methods={
            "Easypaisa": args.easypaisa or "<your easypaisa number>",
            "JazzCash": args.jazzcash or "<your jazzcash number>",
            "Bank transfer": args.bank or "<your IBAN>",
        },
        review_link=args.review_link or "https://g.page/<your-business>/review",
    )
    backend = generate_backend(db_path, profile.name, vertical="hostel")
    for r in DEFAULT_ROOMS:
        backend.insert("rooms", {**r, "status": "active"})
    backend.close()
    # save the profile next to the db for the demo/serve commands
    prof_path = db_path.with_suffix(".profile.json")
    prof_path.write_text(json.dumps({
        "name": profile.name, "owner_phone": profile.owner_phone,
        "payment_methods": profile.payment_methods, "review_link": profile.review_link,
    }, indent=2))
    print(f"🏠 backend generated: {db_path} ({len(DEFAULT_ROOMS)} rooms seeded)")
    print(f"   profile: {prof_path}")
    print("   next: innkeeper demo --db", db_path)
    return 0


def cmd_demo(args) -> int:
    from scripts.demo import run_demo
    run_demo(args.db)
    return 0


def cmd_digest(args) -> int:
    from innkeeper.app import InnkeeperApp
    from innkeeper.transport.mock import MockTransport
    from innkeeper.core.backend import open_backend
    backend = open_backend(args.db)
    prof = json.loads(Path(str(args.db).replace(".db", ".profile.json")).read_text())
    profile = BusinessProfile(name=prof["name"], owner_phone=prof.get("owner_phone", ""),
                              payment_methods=prof.get("payment_methods", {}),
                              review_link=prof.get("review_link", ""))
    app = InnkeeperApp(backend, make_hostel_pack(profile), MockTransport())
    from innkeeper.agent import digest as digest_mod
    print(digest_mod.build_digest(app, args.today))
    app.close()
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="innkeeper", description="The AI innkeeper — the agent that becomes your business system.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init", help="generate a backend for a new business")
    pi.add_argument("name", help="business slug, e.g. oriole-luxury")
    pi.add_argument("--directory", default=".")
    pi.add_argument("--owner-phone", default="")
    pi.add_argument("--easypaisa", default="")
    pi.add_argument("--jazzcash", default="")
    pi.add_argument("--bank", default="")
    pi.add_argument("--review-link", default="")
    pi.set_defaults(fn=cmd_init)

    pd = sub.add_parser("demo", help="run the 30-second simulated WhatsApp demo")
    pd.add_argument("--db", required=True)
    pd.set_defaults(fn=cmd_demo)

    pg = sub.add_parser("digest", help="print today's owner digest")
    pg.add_argument("--db", required=True)
    pg.add_argument("--today", default=None)
    pg.set_defaults(fn=cmd_digest)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
