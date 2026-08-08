"""Tests for the low-level 1C tab-switching primitive (src/win_1c_bot.py).

pyautogui itself is mocked out (no real screen), but ImageNotFoundException
and FailSafeException are the *real* classes from the installed pyautogui
package - the `except pyautogui.XxxException` clauses in the module under
test are exercised against the real thing, not a stand-in class hierarchy
that happens to have a matching name.
"""

from unittest.mock import MagicMock

import pyautogui as real_pyautogui
import pytest

import src.win_1c_bot as win_1c_bot


@pytest.fixture
def templates_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(win_1c_bot, "TEMPLATES_DIR", tmp_path)
    monkeypatch.setattr(win_1c_bot.time, "sleep", lambda *_: None)
    return tmp_path


def make_fake_pyautogui(locate_side_effect):
    fake = MagicMock()
    fake.ImageNotFoundException = real_pyautogui.ImageNotFoundException
    fake.FailSafeException = real_pyautogui.FailSafeException
    fake.locateCenterOnScreen.side_effect = locate_side_effect
    return fake


def test_returns_false_when_pyautogui_unavailable(monkeypatch, templates_dir):
    """Regression: win_1c_bot.py used to `import pyautogui` unguarded, so
    importing it (transitively, via onec_order_entry.py) would crash the
    whole process on any system where pyautogui fails to import - exactly
    the scenario onec_order_entry.py's own `pyautogui = None` guard exists
    to tolerate. Once imported successfully, this is what a caller sees at
    runtime if pyautogui is unavailable."""
    monkeypatch.setattr(win_1c_bot, "pyautogui", None)

    assert win_1c_bot.click_tab_by_image("tab.png") is False


def test_returns_false_when_template_file_missing(monkeypatch, templates_dir):
    """A template file missing from disk is a config/deployment error, not
    a "not currently on screen" miss - must be reported distinctly and
    never even reach pyautogui."""
    fake = make_fake_pyautogui(locate_side_effect=[(1, 1)])
    monkeypatch.setattr(win_1c_bot, "pyautogui", fake)

    result = win_1c_bot.click_tab_by_image("missing.png")

    assert result is False
    fake.locateCenterOnScreen.assert_not_called()


def test_happy_path_clicks_and_returns_true(monkeypatch, templates_dir):
    (templates_dir / "tab.png").write_bytes(b"fake")
    fake = make_fake_pyautogui(locate_side_effect=[(42, 24)])
    monkeypatch.setattr(win_1c_bot, "pyautogui", fake)

    result = win_1c_bot.click_tab_by_image("tab.png", confidence=0.9)

    assert result is True
    fake.click.assert_called_once_with((42, 24))
    fake.locateCenterOnScreen.assert_called_once_with(str(templates_dir / "tab.png"), confidence=0.9)


def test_retries_then_succeeds(monkeypatch, templates_dir):
    (templates_dir / "tab.png").write_bytes(b"fake")
    fake = make_fake_pyautogui(locate_side_effect=[None, (5, 5)])
    monkeypatch.setattr(win_1c_bot, "pyautogui", fake)

    result = win_1c_bot.click_tab_by_image("tab.png", attempts=3, retry_delay=0)

    assert result is True
    assert fake.locateCenterOnScreen.call_count == 2
    fake.click.assert_called_once_with((5, 5))


def test_gives_up_after_all_attempts_exhausted(monkeypatch, templates_dir):
    (templates_dir / "tab.png").write_bytes(b"fake")
    fake = make_fake_pyautogui(locate_side_effect=[None, None, None])
    monkeypatch.setattr(win_1c_bot, "pyautogui", fake)

    result = win_1c_bot.click_tab_by_image("tab.png", attempts=3, retry_delay=0)

    assert result is False
    assert fake.locateCenterOnScreen.call_count == 3
    fake.click.assert_not_called()


def test_handles_image_not_found_exception_with_retry(monkeypatch, templates_dir):
    (templates_dir / "tab.png").write_bytes(b"fake")
    fake = make_fake_pyautogui(locate_side_effect=[real_pyautogui.ImageNotFoundException("nope"), (7, 7)])
    monkeypatch.setattr(win_1c_bot, "pyautogui", fake)

    result = win_1c_bot.click_tab_by_image("tab.png", attempts=3, retry_delay=0)

    assert result is True
    assert fake.locateCenterOnScreen.call_count == 2


def test_failsafe_exception_aborts_immediately_without_retry(monkeypatch, templates_dir, caplog):
    """A human yanking the mouse to a screen corner is a deliberate abort,
    not a vision glitch - must not be retried, and must be logged clearly
    rather than folded into the generic vision-error message."""
    (templates_dir / "tab.png").write_bytes(b"fake")
    fake = make_fake_pyautogui(locate_side_effect=real_pyautogui.FailSafeException("corner"))
    monkeypatch.setattr(win_1c_bot, "pyautogui", fake)

    with caplog.at_level("CRITICAL"):
        result = win_1c_bot.click_tab_by_image("tab.png", attempts=3, retry_delay=0)

    assert result is False
    assert fake.locateCenterOnScreen.call_count == 1
    assert any("fail-safe" in r.message for r in caplog.records)


def test_generic_exception_aborts_immediately_without_retry(monkeypatch, templates_dir):
    (templates_dir / "tab.png").write_bytes(b"fake")
    fake = make_fake_pyautogui(locate_side_effect=RuntimeError("boom"))
    monkeypatch.setattr(win_1c_bot, "pyautogui", fake)

    result = win_1c_bot.click_tab_by_image("tab.png", attempts=3, retry_delay=0)

    assert result is False
    assert fake.locateCenterOnScreen.call_count == 1


def test_confidence_kwarg_does_not_raise_notimplementederror(tmp_path):
    """Regression: pyautogui/pyscreeze's `confidence=` kwarg (passed on
    every real call in click_tab_by_image) silently requires OpenCV -
    without it installed, this raises NotImplementedError on every single
    call, so every real (non-dry-run) 1C tab switch would fail outright.
    Exercises the real pyscreeze + OpenCV stack (no mocking) against two
    tiny synthetic images to prove the dependency is actually present and
    wired up correctly, not just importable."""
    import pyscreeze
    from PIL import Image

    haystack = Image.new("RGB", (50, 50), "white")
    for x in range(20, 30):
        for y in range(20, 30):
            haystack.putpixel((x, y), (255, 0, 0))
    haystack_path = tmp_path / "haystack.png"
    haystack.save(haystack_path)

    needle = Image.new("RGB", (10, 10), (255, 0, 0))
    needle_path = tmp_path / "needle.png"
    needle.save(needle_path)

    box = pyscreeze.locate(str(needle_path), str(haystack_path), confidence=0.8)

    assert box is not None


# --- импорт на машине без дисплея ---

def test_pyautogui_import_guard_catches_more_than_importerror():
    """Регресс: guard ловил только ImportError.

    Когда pyautogui установлен, но дисплея нет, падение происходит внутри
    его собственного импорта — mouseinfo дёргает os.environ['DISPLAY'] на
    уровне модуля и кидает KeyError. ImportError-only guard такое не ловил,
    и процесс умирал на импорте ещё до того, как могла отработать вежливая
    обработка «pyautogui недоступен» — то есть headless-старт (или окно
    между запуском Xvfb и готовностью DISPLAY) ронял бота целиком.
    """
    import ast
    from pathlib import Path

    for module in ("win_1c_bot.py", "onec_order_entry.py", "agent_booked_ocr.py"):
        source = (Path(__file__).resolve().parent.parent / "src" / module).read_text(encoding="utf-8")
        tree = ast.parse(source)

        guards = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Try)
            and any(
                isinstance(stmt, ast.Import) and any(a.name == "pyautogui" for a in stmt.names)
                for stmt in node.body
            )
        ]
        assert guards, f"{module}: import pyautogui не обёрнут в try/except"

        caught = {
            handler.type.id
            for guard in guards for handler in guard.handlers
            if isinstance(handler.type, ast.Name)
        }
        assert "Exception" in caught, f"{module}: guard ловит {caught}, а нужен Exception"
