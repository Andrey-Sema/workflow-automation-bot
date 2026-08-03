"""Shared concurrency primitives for the order flow.

Pulled into their own module (instead of living as module-level globals in
`handlers/orders.py`) so `bot.py` can also see `onec_entry_lock` during
shutdown, without reaching into another module's "private" underscore
names.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

# aiogram dispatches updates from the same chat as independent concurrent
# tasks (confirmed by reading Dispatcher._polling: each update becomes its
# own asyncio.create_task, tracked only in a set with no ordering
# guarantee) — e.g. a multi-photo Telegram "album" arrives as several
# updates in quick succession. One lock per chat_id serializes the
# read-modify-write of FSM data for that chat.
chat_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

# Only one наряд can physically be typed into 1C at a time — it's a single
# RDP screen. Also awaited (not just acquired) during shutdown — see
# bot.py — so an in-flight entry gets a chance to finish instead of being
# cut off mid-keystroke-sequence by a container stop.
onec_entry_lock = asyncio.Lock()
