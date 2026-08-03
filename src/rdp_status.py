"""Reads `docker/entrypoint.py`'s `.rdp_status` file — the one channel the
Python process has into whether the FreeRDP session the container
supervises is actually connected right now. Used both for the `/status`
admin command and as a hard safety gate before any real (non-dry-run) 1C
keystroke automation: typing blind into a screen that isn't actually
showing 1C would be worse than not typing at all.
"""

from __future__ import annotations

from src.settings import Settings

CONNECTED = "connected"
CONNECTING = "connecting"
DISABLED = "disabled"
UNKNOWN = "unknown"


def read_rdp_status(settings: Settings) -> str:
    """Raw status string, or "unknown" if the file doesn't exist (e.g. not
    running under docker/entrypoint.py — plain local CLI usage)."""
    status_path = settings.data_dir / ".rdp_status"
    if not status_path.exists():
        return UNKNOWN
    try:
        return status_path.read_text(encoding="utf-8").strip()
    except OSError:
        return UNKNOWN


def is_rdp_connected(settings: Settings) -> bool:
    return read_rdp_status(settings) == CONNECTED


def describe_rdp_status(settings: Settings) -> str:
    """Human-friendly Russian description for /status and error messages."""
    raw = read_rdp_status(settings)
    icons = {
        CONNECTED: "✅ подключен",
        CONNECTING: "🟡 подключение...",
        DISABLED: "⚪ отключен (RDP_HOST не задан)",
        UNKNOWN: "❔ неизвестен (не в Docker или ещё не запускался)",
    }
    return icons.get(raw, f"🔴 {raw}")
