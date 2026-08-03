"""Runtime-editable operator allowlist (data/operators.json), additive to
the `.env`-configured TELEGRAM_OPERATOR_IDS. Admin ids stay `.env`-only —
granting admin rights is a deliberate, restart-required action, not
something one Telegram command should be able to do to itself.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class OperatorsStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> list[int]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.error("⚠️ Не удалось прочитать %s, считаю список пустым", self.path)
            return []

    def _write(self, ids: list[int]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(sorted(set(ids)), ensure_ascii=False, indent=2), encoding="utf-8")

    def list(self) -> list[int]:
        return self._read()

    def add(self, chat_id: int) -> None:
        ids = set(self._read())
        ids.add(chat_id)
        self._write(list(ids))

    def remove(self, chat_id: int) -> bool:
        ids = set(self._read())
        if chat_id not in ids:
            return False
        ids.discard(chat_id)
        self._write(list(ids))
        return True
