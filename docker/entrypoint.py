#!/usr/bin/env python3
"""Container entrypoint: starts Xvfb (virtual display), optionally a noVNC
viewer, keeps a FreeRDP session alive against the configured Windows/1C
host, and runs the Telegram bot as the long-lived foreground process.

Deliberately a Python script, not a shell script: every subprocess is
launched with a fixed argument list (never `shell=True`), so none of this
is vulnerable to shell injection via environment variables, and RDP_PASSWORD
is fed to xfreerdp3 over a pipe (`/from-stdin`) instead of ever appearing as
a CLI argument — `ps aux` inside the container never shows it.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

DISPLAY = os.environ.get("DISPLAY", ":99")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
RDP_STATUS_PATH = DATA_DIR / ".rdp_status"

children: list[subprocess.Popen] = []
_shutting_down = threading.Event()


def _spawn(cmd: list[str], **kwargs) -> subprocess.Popen:
    proc = subprocess.Popen(cmd, **kwargs)
    children.append(proc)
    return proc


def _handle_signal(signum, frame) -> None:
    _shutting_down.set()
    for proc in list(children):
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()


def _wait_for_display(timeout: float = 15) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = subprocess.run(
            ["xdpyinfo", "-display", DISPLAY], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
        )
        if result.returncode == 0:
            return
        time.sleep(0.5)
    raise RuntimeError(f"Xvfb на {DISPLAY} не поднялся за {timeout}с")


def start_xvfb() -> None:
    width = os.environ.get("RDP_WIDTH", "1920")
    height = os.environ.get("RDP_HEIGHT", "1080")
    _spawn(["Xvfb", DISPLAY, "-screen", "0", f"{width}x{height}x24", "-nolisten", "tcp"])
    _wait_for_display()
    print(f"🖥️  Xvfb готов на {DISPLAY} ({width}x{height})", flush=True)


def start_debug_vnc() -> None:
    if os.environ.get("DEBUG_VNC", "false").lower() != "true":
        return
    _spawn(["x11vnc", "-display", DISPLAY, "-forever", "-shared", "-nopw", "-rfbport", "5900", "-quiet"])
    _spawn(["websockify", "--web=/usr/share/novnc", "6080", "localhost:5900"])
    print("👁️  noVNC доступен на порту 6080 (DEBUG_VNC=true)", flush=True)


def _read_rdp_password() -> str:
    password_file = os.environ.get("RDP_PASSWORD_FILE")
    if password_file and Path(password_file).exists():
        return Path(password_file).read_text(encoding="utf-8").strip()
    return os.environ.get("RDP_PASSWORD", "")


def build_rdp_command() -> list[str] | None:
    host = os.environ.get("RDP_HOST")
    if not host:
        print("⚠️  RDP_HOST не задан — пропускаю подключение (для локальной отладки бота без 1С).", flush=True)
        return None

    user = os.environ.get("RDP_USER", "")
    domain = os.environ.get("RDP_DOMAIN", "")
    width = os.environ.get("RDP_WIDTH", "1920")
    height = os.environ.get("RDP_HEIGHT", "1080")

    cmd = [
        "xfreerdp3", f"/v:{host}", f"/u:{user}", f"/w:{width}", f"/h:{height}",
        "/cert:ignore", "/from-stdin", "+auto-reconnect", "/auto-reconnect-max-retries:0",
        "-sound", "-microphone", "-printer", "-smartcard",
    ]
    if domain:
        cmd.append(f"/d:{domain}")
    return cmd


def rdp_supervisor_loop() -> None:
    """Keeps a FreeRDP session alive against RDP_HOST, restarting with
    backoff if it drops. Writes a one-word status to .rdp_status so the
    bot's /status command can report RDP connectivity.
    """
    cmd = build_rdp_command()
    if cmd is None:
        RDP_STATUS_PATH.write_text("disabled", encoding="utf-8")
        return

    backoff = 2.0
    while not _shutting_down.is_set():
        RDP_STATUS_PATH.write_text("connecting", encoding="utf-8")
        password = _read_rdp_password()
        proc = _spawn(cmd, stdin=subprocess.PIPE, text=True, env={**os.environ, "DISPLAY": DISPLAY})
        with contextlib.suppress(BrokenPipeError, OSError):
            if proc.stdin:
                proc.stdin.write(password + "\n")
                proc.stdin.flush()
                proc.stdin.close()

        RDP_STATUS_PATH.write_text("connected", encoding="utf-8")
        returncode = proc.wait()
        if proc in children:
            children.remove(proc)

        if _shutting_down.is_set():
            return

        RDP_STATUS_PATH.write_text(f"disconnected (code {returncode})", encoding="utf-8")
        print(f"⚠️  xfreerdp3 завершился (код {returncode}), переподключение через {backoff:.0f}с...", flush=True)
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    start_xvfb()
    start_debug_vnc()

    threading.Thread(target=rdp_supervisor_loop, daemon=True).start()

    print("🚀 Запускаю Telegram-бота...", flush=True)
    bot_proc = _spawn([sys.executable, "-m", "src.telegram_bot"], env={**os.environ, "DISPLAY": DISPLAY})
    returncode = bot_proc.wait()

    _shutting_down.set()
    for proc in list(children):
        if proc.poll() is None:
            proc.terminate()
    return returncode


if __name__ == "__main__":
    sys.exit(main())
