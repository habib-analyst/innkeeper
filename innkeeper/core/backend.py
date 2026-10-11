"""Generated business backend: the agent IS the system of record.

On `innkeeper init my-guesthouse`, this generates a SQLite database with the
schema the vertical skill pack declares: booking ledger, guest CRM, room
inventory, staff task board. The business owns its data — export anytime.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Schema registry: vertical skill packs declare their tables here.
# ---------------------------------------------------------------------------

HOSTEL_SCHEMA: List[Dict[str, Any]] = [
    {
        "name": "rooms",
        "columns": [
            ("id", "TEXT PRIMARY KEY"),
            ("name", "TEXT NOT NULL"),
            ("room_type", "TEXT NOT NULL"),          # single/double/deluxe/dorm
            ("capacity", "INTEGER NOT NULL"),
            ("base_price_pkr", "INTEGER NOT NULL"),
            ("status", "TEXT NOT NULL DEFAULT 'active'"),  # active/maintenance
        ],
    },
    {
        "name": "guests",
        "columns": [
            ("id", "TEXT PRIMARY KEY"),
            ("name", "TEXT NOT NULL"),
            ("phone", "TEXT NOT NULL"),
            ("notes", "TEXT DEFAULT ''"),
            ("total_stays", "INTEGER NOT NULL DEFAULT 0"),
            ("created_at", "TEXT NOT NULL"),
        ],
    },
    {
        "name": "bookings",
        "columns": [
            ("id", "TEXT PRIMARY KEY"),
            ("guest_id", "TEXT NOT NULL REFERENCES guests(id)"),
            ("room_id", "TEXT NOT NULL REFERENCES rooms(id)"),
            ("check_in", "TEXT NOT NULL"),            # ISO date
            ("check_out", "TEXT NOT NULL"),           # ISO date
            ("guests_count", "INTEGER NOT NULL DEFAULT 1"),
            ("total_pkr", "INTEGER NOT NULL"),
            ("status", "TEXT NOT NULL DEFAULT 'confirmed'"),
            # confirmed / checked_in / checked_out / cancelled / no_show
            ("source", "TEXT NOT NULL DEFAULT 'whatsapp'"),
            ("created_at", "TEXT NOT NULL"),
        ],
    },
    {
        "name": "staff_tasks",
        "columns": [
            ("id", "TEXT PRIMARY KEY"),
            ("kind", "TEXT NOT NULL"),                # housekeeping/checkin_prep/maintenance/other
            ("room_id", "TEXT REFERENCES rooms(id)"),
            ("booking_id", "TEXT REFERENCES bookings(id)"),
            ("detail", "TEXT NOT NULL"),
            ("assignee", "TEXT DEFAULT ''"),
            ("status", "TEXT NOT NULL DEFAULT 'open'"),  # open/done
            ("due_at", "TEXT DEFAULT ''"),
            ("created_at", "TEXT NOT NULL"),
        ],
    },
    {
        "name": "payments",
        "columns": [
            ("id", "TEXT PRIMARY KEY"),
            ("booking_id", "TEXT NOT NULL REFERENCES bookings(id)"),
            ("amount_pkr", "INTEGER NOT NULL"),
            ("method", "TEXT NOT NULL"),              # easypaisa/jazzcash/bank/cash
            ("status", "TEXT NOT NULL DEFAULT 'pending'"),  # pending/confirmed/refunded
            ("created_at", "TEXT NOT NULL"),
        ],
    },
    {
        "name": "reviews",
        "columns": [
            ("id", "TEXT PRIMARY KEY"),
            ("booking_id", "TEXT NOT NULL REFERENCES bookings(id)"),
            ("requested_at", "TEXT NOT NULL"),
            ("responded", "INTEGER NOT NULL DEFAULT 0"),
            ("rating", "INTEGER"),
            ("channel", "TEXT DEFAULT 'google'"),
        ],
    },
]

SCHEMAS: Dict[str, List[Dict[str, Any]]] = {
    "hostel": HOSTEL_SCHEMA,
    # restaurant / clinic packs register here later — the core stays vertical-agnostic.
}


@dataclass
class BusinessBackend:
    """A generated, business-owned SQLite backend."""

    path: Path
    vertical: str
    business_name: str
    _conn: Optional[sqlite3.Connection] = field(default=None, repr=False)

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.commit()
            self._conn.close()
            self._conn = None

    # -- generic data access -------------------------------------------------
    def insert(self, table: str, row: Dict[str, Any]) -> None:
        cols = ", ".join(row.keys())
        qs = ", ".join("?" for _ in row)
        self.conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", list(row.values()))
        self.conn.commit()

    def update(self, table: str, where: str, args: List[Any], patch: Dict[str, Any]) -> int:
        sets = ", ".join(f"{k}=?" for k in patch)
        cur = self.conn.execute(
            f"UPDATE {table} SET {sets} WHERE {where}", list(patch.values()) + args
        )
        self.conn.commit()
        return cur.rowcount

    def query(self, sql: str, args: List[Any] | tuple = ()) -> List[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchall()

    def query_one(self, sql: str, args: List[Any] | tuple = ()) -> Optional[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchone()

    # -- domain helpers ------------------------------------------------------
    def rooms_available(self, check_in: str, check_out: str, room_type: Optional[str] = None) -> List[sqlite3.Row]:
        """Rooms with no overlapping confirmed/checked-in booking in [check_in, check_out)."""
        q = """
        SELECT r.* FROM rooms r WHERE r.status='active'
        """
        args: List[Any] = []
        if room_type:
            q += " AND r.room_type=?"
            args.append(room_type)
        q += """
        AND NOT EXISTS (
            SELECT 1 FROM bookings b
            WHERE b.room_id = r.id
              AND b.status IN ('confirmed','checked_in')
              AND b.check_in < ? AND b.check_out > ?
        )
        """
        args += [check_out, check_in]
        return self.query(q, args)

    def export_json(self) -> Dict[str, Any]:
        """Full export — the business owns its data, always."""
        out: Dict[str, Any] = {"business": self.business_name, "vertical": self.vertical, "tables": {}}
        for (name,) in self.query("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            out["tables"][name] = [dict(r) for r in self.query(f"SELECT * FROM {name}")]
        return out


def generate_backend(db_path: str | Path, business_name: str, vertical: str = "hostel") -> BusinessBackend:
    """Create a fresh backend database for a business. Idempotent-safe: refuses to overwrite."""
    db_path = Path(db_path)
    if db_path.exists():
        raise FileExistsError(f"backend already exists at {db_path} — refusing to overwrite")
    if vertical not in SCHEMAS:
        raise ValueError(f"unknown vertical {vertical!r}; known: {sorted(SCHEMAS)}")
    backend = BusinessBackend(path=db_path, vertical=vertical, business_name=business_name)
    for table in SCHEMAS[vertical]:
        cols = ", ".join(f"{name} {ctype}" for name, ctype in table["columns"])
        backend.conn.execute(f"CREATE TABLE {table['name']} ({cols})")
    backend.conn.execute(
        "CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT)"
    )
    backend.conn.execute(
        "INSERT INTO _meta (key, value) VALUES (?, ?)",
        ("schema", json.dumps({"business": business_name, "vertical": vertical})),
    )
    backend.conn.commit()
    return backend


def open_backend(db_path: str | Path) -> BusinessBackend:
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"no backend at {db_path} — run `innkeeper init` first")
    row = sqlite3.connect(str(db_path)).execute("SELECT value FROM _meta WHERE key='schema'").fetchone()
    meta = json.loads(row[0]) if row else {"business": db_path.stem, "vertical": "hostel"}
    return BusinessBackend(path=db_path, vertical=meta["vertical"], business_name=meta["business"])
