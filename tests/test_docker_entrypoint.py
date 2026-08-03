"""Unit tests for the pure/testable parts of docker/entrypoint.py (command
construction, password source precedence). Process supervision itself
needs a real container and isn't covered here."""

import importlib.util
import sys
from pathlib import Path

_ENTRYPOINT_PATH = Path(__file__).resolve().parent.parent / "docker" / "entrypoint.py"
_spec = importlib.util.spec_from_file_location("docker_entrypoint", _ENTRYPOINT_PATH)
entrypoint = importlib.util.module_from_spec(_spec)
sys.modules["docker_entrypoint"] = entrypoint
_spec.loader.exec_module(entrypoint)


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
