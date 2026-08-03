"""Unit tests for the pure/testable parts of docker/entrypoint.py (command
construction, password source precedence, shutdown sequencing). Process
supervision itself needs a real container and isn't covered here."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ENTRYPOINT_PATH = Path(__file__).resolve().parent.parent / "docker" / "entrypoint.py"
_spec = importlib.util.spec_from_file_location("docker_entrypoint", _ENTRYPOINT_PATH)
entrypoint = importlib.util.module_from_spec(_spec)
sys.modules["docker_entrypoint"] = entrypoint
_spec.loader.exec_module(entrypoint)


@pytest.fixture(autouse=True)
def _reset_module_state():
    # entrypoint.py is loaded once (module-level import above) and shared
    # across every test in this file - reset its mutable globals so tests
    # can't leak process lists / shutdown flags into each other.
    entrypoint.children.clear()
    entrypoint._bot_proc = None
    entrypoint._shutting_down.clear()
    yield
    entrypoint.children.clear()
    entrypoint._bot_proc = None
    entrypoint._shutting_down.clear()


def test_build_rdp_command_none_without_host(monkeypatch):
    monkeypatch.delenv("RDP_HOST", raising=False)
    assert entrypoint.build_rdp_command() is None


def test_build_rdp_command_includes_host_user_resolution(monkeypatch):
    monkeypatch.setenv("RDP_HOST", "192.168.1.50")
    monkeypatch.setenv("RDP_USER", "operator")
    monkeypatch.setenv("RDP_WIDTH", "1600")
    monkeypatch.setenv("RDP_HEIGHT", "900")
    monkeypatch.delenv("RDP_DOMAIN", raising=False)

    cmd = entrypoint.build_rdp_command()

    assert cmd[0] == "xfreerdp3"
    assert "/v:192.168.1.50" in cmd
    assert "/u:operator" in cmd
    assert "/w:1600" in cmd
    assert "/h:900" in cmd
    assert "/from-stdin" in cmd
    assert not any(part.startswith("/d:") for part in cmd)


def test_build_rdp_command_includes_domain_when_set(monkeypatch):
    monkeypatch.setenv("RDP_HOST", "host")
    monkeypatch.setenv("RDP_DOMAIN", "CORP")

    cmd = entrypoint.build_rdp_command()

    assert "/d:CORP" in cmd


def test_build_rdp_command_never_includes_password():
    """Regression guard: the password must only ever go over stdin, never
    as a CLI argument (visible in `ps aux` inside the container)."""
    import os

    os.environ["RDP_HOST"] = "host"
    os.environ["RDP_PASSWORD"] = "super-secret-value"
    try:
        cmd = entrypoint.build_rdp_command()
        assert not any("super-secret-value" in part for part in cmd)
    finally:
        del os.environ["RDP_PASSWORD"]
        del os.environ["RDP_HOST"]


def test_read_rdp_password_prefers_file(tmp_path, monkeypatch):
    secret_file = tmp_path / "rdp_password"
    secret_file.write_text("from-file\n", encoding="utf-8")
    monkeypatch.setenv("RDP_PASSWORD_FILE", str(secret_file))
    monkeypatch.setenv("RDP_PASSWORD", "from-env")

    assert entrypoint._read_rdp_password() == "from-file"


def test_read_rdp_password_falls_back_to_env(monkeypatch):
    monkeypatch.delenv("RDP_PASSWORD_FILE", raising=False)
    monkeypatch.setenv("RDP_PASSWORD", "from-env")

    assert entrypoint._read_rdp_password() == "from-env"


def test_read_rdp_password_missing_file_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("RDP_PASSWORD_FILE", "/nonexistent/path")
    monkeypatch.setenv("RDP_PASSWORD", "from-env")

    assert entrypoint._read_rdp_password() == "from-env"


def test_shutdown_signal_terminates_only_bot_when_bot_is_running():
    """Regression: killing Xvfb/xfreerdp3 at the same moment as the bot
    process would race the bot's own graceful shutdown (it needs the
    display alive to finish typing into 1C first). Only the bot should be
    signaled directly; main()'s tail terminates the rest once the bot
    actually exits.
    """
    fake_xvfb = MagicMock()
    fake_rdp = MagicMock()
    fake_bot = MagicMock()
    entrypoint.children.extend([fake_xvfb, fake_rdp, fake_bot])
    entrypoint._bot_proc = fake_bot

    entrypoint._handle_signal(15, None)

    fake_bot.terminate.assert_called_once()
    fake_xvfb.terminate.assert_not_called()
    fake_rdp.terminate.assert_not_called()
    assert entrypoint._shutting_down.is_set()


def test_rdp_supervisor_never_reports_connected_if_process_dies_in_grace_window(monkeypatch, tmp_path):
    """Regression: a bad-password/unreachable-host xfreerdp3 exits almost
    immediately. .rdp_status must never say "connected" for a process that
    never survived the grace window - src/rdp_status.py's safety gate
    trusts that file before allowing real 1C keystrokes."""
    monkeypatch.setenv("RDP_HOST", "host")
    monkeypatch.setattr(entrypoint, "RDP_CONNECT_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(entrypoint, "RDP_STATUS_PATH", tmp_path / ".rdp_status")

    fake_proc = MagicMock()
    fake_proc.stdin = MagicMock()
    fake_proc.poll.return_value = 1  # already exited by the time we check
    fake_proc.returncode = 1

    monkeypatch.setattr(entrypoint, "_spawn", lambda *a, **k: fake_proc)

    statuses_written = []
    real_write_text = Path.write_text

    def tracking_write_text(self, content, **kwargs):
        if self == entrypoint.RDP_STATUS_PATH:
            statuses_written.append(content)
        return real_write_text(self, content, **kwargs)

    monkeypatch.setattr(Path, "write_text", tracking_write_text)

    def stop_after_one_iteration(*a, **k):
        entrypoint._shutting_down.set()

    monkeypatch.setattr(entrypoint.time, "sleep", stop_after_one_iteration)

    entrypoint.rdp_supervisor_loop()

    assert "connected" not in statuses_written
    assert any(s.startswith("disconnected") for s in statuses_written)


def test_rdp_supervisor_reports_connected_once_process_survives_grace_window(monkeypatch, tmp_path):
    monkeypatch.setenv("RDP_HOST", "host")
    monkeypatch.setattr(entrypoint, "RDP_CONNECT_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(entrypoint, "RDP_STATUS_PATH", tmp_path / ".rdp_status")

    fake_proc = MagicMock()
    fake_proc.stdin = MagicMock()
    fake_proc.poll.return_value = None  # still running throughout

    def fake_wait():
        entrypoint._shutting_down.set()  # stop the loop after this "session"
        return 0

    fake_proc.wait.side_effect = fake_wait

    monkeypatch.setattr(entrypoint, "_spawn", lambda *a, **k: fake_proc)

    statuses_written = []
    real_write_text = Path.write_text

    def tracking_write_text(self, content, **kwargs):
        if self == entrypoint.RDP_STATUS_PATH:
            statuses_written.append(content)
        return real_write_text(self, content, **kwargs)

    monkeypatch.setattr(Path, "write_text", tracking_write_text)

    entrypoint.rdp_supervisor_loop()

    assert "connected" in statuses_written
    assert statuses_written.index("connected") > statuses_written.index("connecting")


def test_shutdown_signal_terminates_everything_if_bot_not_started_yet():
    """If the signal arrives during Xvfb/RDP startup (before the bot
    process exists), there's nothing graceful to wait for - tear down
    whatever did start."""
    fake_xvfb = MagicMock()
    fake_rdp = MagicMock()
    entrypoint.children.extend([fake_xvfb, fake_rdp])

    entrypoint._handle_signal(15, None)

    fake_xvfb.terminate.assert_called_once()
    fake_rdp.terminate.assert_called_once()
