"""Persistent aiogram FSM storage backed by SQLite (via aiosqlite).

aiogram's default `MemoryStorage` documents itself as unfit for
production: "all data is lost when the bot restarts". For this bot that
meant an in-progress photo-collection draft (and its downloaded files
under `data/incoming/`) silently vanished on every container restart or
deploy, with the operator having no idea their draft was gone until they
tried to continue it. Same aiosqlite-per-call + WAL pattern already used
by `src.order_store.OrderStore`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import aiosqlite
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fsm_storage (
    storage_key TEXT PRIMARY KEY,
    state TEXT,
    data TEXT NOT NULL DEFAULT '{}'
);
"""


def _serialize_key(key: StorageKey) -> str:
    return ":".join(
        str(part)
        for part in (key.bot_id, key.chat_id, key.user_id, key.thread_id, key.business_connection_id, key.destiny)
    )


class SQLiteStorage(BaseStorage):
    """Drop-in replacement for aiogram's `MemoryStorage` that survives
    container restarts. Call `init()` once before handing this to
    `Dispatcher(storage=...)`.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript(_SCHEMA)
            await db.commit()

    async def close(self) -> None:
        pass  # every call opens/closes its own short-lived connection

    async def set_state(self, key: StorageKey, state: Any = None) -> None:
        state_str = state.state if isinstance(state, State) else state
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO fsm_storage (storage_key, state, data) VALUES (?, ?, '{}') "
                "ON CONFLICT(storage_key) DO UPDATE SET state = excluded.state",
                (_serialize_key(key), state_str),
            )
            await db.commit()

    async def get_state(self, key: StorageKey) -> str | None:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT state FROM fsm_storage WHERE storage_key = ?", (_serialize_key(key),))
            row = await cursor.fetchone()
        return row[0] if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        if not isinstance(data, dict):
            data = dict(data)
        payload = json.dumps(data, ensure_ascii=False)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO fsm_storage (storage_key, state, data) VALUES (?, NULL, ?) "
                "ON CONFLICT(storage_key) DO UPDATE SET data = excluded.data",
                (_serialize_key(key), payload),
            )
            await db.commit()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT data FROM fsm_storage WHERE storage_key = ?", (_serialize_key(key),))
            row = await cursor.fetchone()
        return json.loads(row[0]) if row and row[0] else {}
