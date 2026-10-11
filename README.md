# innkeeper

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) [![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)] [![GitHub stars](https://img.shields.io/github/stars/habib-analyst/innkeeper.svg)](https://github.com/habib-analyst/innkeeper)

**The AI innkeeper: the agent that *becomes* your business system.** No PMS, no POS, no problem — on signup it generates your backend (bookings, guests, rooms, staff tasks), runs your whole operation over WhatsApp, and journals every action as a reversible transaction. Built for the guest houses, restaurants, and clinics that run on WhatsApp and spreadsheets — the businesses every existing AI tool ignores because there's "nothing to integrate with."

## Quickstart

```bash
git clone https://github.com/habib-analyst/innkeeper.git
cd innkeeper
pip install -e .
innkeeper init my-guesthouse --owner-phone +923001234567 --easypaisa 0345-1111111
innkeeper demo --db my-guesthouse.db
```

Real output — a full guest journey through a simulated WhatsApp conversation:

```
🏠 backend generated: my-guesthouse.db (5 rooms seeded)
...
🧑 +923001234567: Salam! Do you have a room for 2 people, tomorrow to day after tomorrow?
🏠 innkeeper: ✅ Available 2026-10-12 → 2026-10-13:
  • Deluxe 1 — Rs 8,500/night
  • Standard 1 — Rs 5,500/night
🧑 +923001234567: Deluxe 1 sounds good
🏠 innkeeper: ✅ Booked — Deluxe 1, 1 night, Rs 8,500. Pay on arrival or Easypaisa 0345-1111111.
   Booking #BK-0007 · undo: innkeeper undo BK-0007
```

Roman Urdu works too ("mujhe kal ke liye room chahiye") — the NLU doesn't care which script you type in.

## Measured results

From the scripted evaluation (10 scripted guest scenarios, 2026-10-11):

| Metric | Result |
|---|---|
| Scenarios completed | 10/10 |
| Booking conversion | 1.00 |
| Agent response time | p95 4.73 ms |
| Transaction journal | 3/3 undos exact (byte-for-byte state reversal) |
| HITL escalation | 1.00 (refunds and destructive actions always ask the owner) |
| Test suite | 21/21 green |

## How it works

- **Generated backend** — `innkeeper init` creates a SQLite schema for your business (rooms, bookings, guests, staff tasks) from a profile; no migrations, no ORM.
- **Reversible journal** — every mutating action is journaled with its inverse; `innkeeper undo BK-0007` restores the exact prior state.
- **HITL tiers** — routine actions auto-execute; destructive ones (refunds, cancellations) escalate to the owner's phone before anything changes.
- **WhatsApp-native** — the whole interaction surface is chat messages; nothing to install for the guest.

## Limitations

- WhatsApp is currently **simulated**: live messaging needs his one-time Meta WhatsApp Cloud API verification, which hasn't happened yet. The full flow runs end-to-end in simulation mode; nothing about the API surface changes when the real key arrives.
- Day-one scope is a hostel/guest-house pack. Restaurant and clinic packs are planned, not built.

---
Built by [Habib Ur Rehman](https://github.com/habib-analyst) · ML/AI Engineer & healthcare-AI researcher ·
[LinkedIn](https://www.linkedin.com/in/hur-dev) · [habib.top](https://habib.top)
