"""Lightweight order/session persistence (SQLite via aiosqlite).

Tracks the lifecycle of each order the Telegram bot processes — from
"pending confirmation" (summary shown, waiting for the operator to tap
✅/❌) through "entered"/"cancelled"/"failed" — so `/status` and
"cancel the last order" have something to look at, and so an audit trail
survives a container restart (the sqlite file lives under the same
`data/` volume as everything else).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

PENDING = "pending_confirmation"
ENTERED = "entered"
CANCELLED = "cancelled"
FAILED = "failed"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    deceased_fio TEXT,
    handwritten_total INTEGER,
    calculated_total INTEGER,
    num_addresses INTEGER,
    photo_paths TEXT NOT NULL,
    order_data TEXT NOT NULL,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_chat_status ON orders (chat_id, status);
"""


@dataclass
class OrderRecord:
    order_id: str
    chat_id: int
    status: str
    deceased_fio: str
    handwritten_total: int
    calculated_total: int
    num_addresses: int
    photo_paths: list[str] = field(default_factory=list)
    order_data: dict = field(default_factory=dict)
    error_message: str | None = None
    created_at: str = ""
    updated_at: str = ""


class OrderStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def create_pending(
        self, order_id: str, chat_id: int, photo_paths: list[str], num_addresses: int, order_data: dict, summary
    ) -> OrderRecord:
        now = datetime.now(UTC).isoformat()
        record = OrderRecord(
            order_id=order_id,
            chat_id=chat_id,
            status=PENDING,
            deceased_fio=summary.deceased_fio,
            handwritten_total=summary.handwritten_total,
            calculated_total=summary.calculated_total,
            num_addresses=num_addresses,
            photo_paths=photo_paths,
            order_data=order_data,
            created_at=now,
            updated_at=now,
        )
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO orders
                   (order_id, chat_id, created_at, updated_at, status, deceased_fio,
                    handwritten_total, calculated_total, num_addresses, photo_paths, order_data, error_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.order_id, record.chat_id, record.created_at, record.updated_at, record.status,
                    record.deceased_fio, record.handwritten_total, record.calculated_total, record.num_addresses,
                    json.dumps(record.photo_paths), json.dumps(record.order_data, ensure_ascii=False), None,
                ),
            )
            await db.commit()
        return record

    async def set_status(self, order_id: str, status: str, error_message: str | None = None) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE orders SET status = ?, error_message = ?, updated_at = ? WHERE order_id = ?",
                (status, error_message, datetime.now(UTC).isoformat(), order_id),
            )
            await db.commit()

    async def get(self, order_id: str) -> OrderRecord | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,))
            row = await cursor.fetchone()
        return _row_to_record(row) if row else None

    async def latest_pending_for_chat(self, chat_id: int) -> OrderRecord | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM orders WHERE chat_id = ? AND status = ? ORDER BY created_at DESC LIMIT 1",
                (chat_id, PENDING),
            )
            row = await cursor.fetchone()
        return _row_to_record(row) if row else None

    async def recent(self, chat_id: int | None = None, limit: int = 10) -> list[OrderRecord]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if chat_id is not None:
                cursor = await db.execute(
                    "SELECT * FROM orders WHERE chat_id = ? ORDER BY created_at DESC LIMIT ?", (chat_id, limit)
                )
            else:
                cursor = await db.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,))
            rows = await cursor.fetchall()
        return [_row_to_record(row) for row in rows]


def _row_to_record(row: aiosqlite.Row) -> OrderRecord:
    return OrderRecord(
        order_id=row["order_id"],
        chat_id=row["chat_id"],
        status=row["status"],
        deceased_fio=row["deceased_fio"] or "",
        handwritten_total=row["handwritten_total"] or 0,
        calculated_total=row["calculated_total"] or 0,
        num_addresses=row["num_addresses"] or 0,
        photo_paths=json.loads(row["photo_paths"]) if row["photo_paths"] else [],
        order_data=json.loads(row["order_data"]) if row["order_data"] else {},
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
