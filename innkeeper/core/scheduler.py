"""Proactive scheduler: the agent doesn't just react, it acts on a clock.

Day-one jobs: post-checkout review requests, upcoming-arrival prep tasks,
payment reminders, daily owner digest. Jobs are idempotent (a `scheduler_runs`
guard table prevents double-firing) and every fired action goes through the
transaction journal like everything else.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List


class Scheduler:
    def __init__(self, app: "InnkeeperApp"):  # noqa: F821
        self.app = app
        self.jobs: List[Dict] = []
        app.backend.conn.execute(
            """CREATE TABLE IF NOT EXISTS _scheduler_runs (
                job TEXT NOT NULL, day TEXT NOT NULL, PRIMARY KEY (job, day))"""
        )
        app.backend.conn.commit()

    def every_day(self, name: str, fn: Callable[[], List[str]]) -> None:
        self.jobs.append({"name": name, "fn": fn})

    def run_due(self, today: str | None = None) -> Dict[str, List[str]]:
        """Run each job at most once per day. Returns {job: [messages produced]}."""
        today = today or date.today().isoformat()
        out: Dict[str, List[str]] = {}
        for job in self.jobs:
            already = self.app.backend.query_one(
                "SELECT 1 FROM _scheduler_runs WHERE job=? AND day=?", [job["name"], today]
            )
            if already:
                continue
            produced = job["fn"]() or []
            out[job["name"]] = produced
            self.app.backend.conn.execute(
                "INSERT OR IGNORE INTO _scheduler_runs (job, day) VALUES (?,?)",
                [job["name"], today],
            )
            self.app.backend.conn.commit()
        return out
