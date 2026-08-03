from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.errors import FileIntakeError
from src.telegram_bot.file_intake import download_order_photo


def fake_bot():
    bot = SimpleNamespace()
    bot.download = AsyncMock()
    return bot


async def test_downloads_photo_with_generated_filename(tmp_path):
    bot = fake_bot()
    message = SimpleNamespace(photo=[SimpleNamespace(file_id="f1", file_size=1000)], document=None)

    path = await download_order_photo(bot, message, tmp_path, max_file_mb=20)

    assert path.parent == tmp_path
    assert path.suffix == ".jpg"
    bot.download.assert_awaited_once_with("f1", destination=path)


async def test_rejects_oversized_photo(tmp_path):
    bot = fake_bot()
    message = SimpleNamespace(photo=[SimpleNamespace(file_id="f1", file_size=50 * 1024 * 1024)], document=None)

    with pytest.raises(FileIntakeError):
        await download_order_photo(bot, message, tmp_path, max_file_mb=20)
    bot.download.assert_not_awaited()


async def test_downloads_pdf_document(tmp_path):
    bot = fake_bot()
    message = SimpleNamespace(
        photo=None,
        document=SimpleNamespace(file_id="d1", file_size=1000, mime_type="application/pdf"),
    )

    path = await download_order_photo(bot, message, tmp_path, max_file_mb=20)
    assert path.suffix == ".pdf"


async def test_rejects_unsupported_mime_type(tmp_path):
    bot = fake_bot()
    message = SimpleNamespace(
        photo=None,
        document=SimpleNamespace(file_id="d1", file_size=1000, mime_type="application/x-msdownload"),
    )

    with pytest.raises(FileIntakeError):
        await download_order_photo(bot, message, tmp_path, max_file_mb=20)
    bot.download.assert_not_awaited()


async def test_ignores_user_supplied_file_name_entirely(tmp_path):
    """Regression guard: destination must never derive from a
    sender-controlled file_name (path traversal vector)."""
    bot = fake_bot()
    message = SimpleNamespace(
        photo=None,
        document=SimpleNamespace(
            file_id="d1", file_size=1000, mime_type="image/png", file_name="../../../etc/passwd"
        ),
    )

    path = await download_order_photo(bot, message, tmp_path, max_file_mb=20)
    assert path.parent == tmp_path
    assert ".." not in str(path)


async def test_no_photo_or_document_raises(tmp_path):
    bot = fake_bot()
    message = SimpleNamespace(photo=None, document=None)
    with pytest.raises(FileIntakeError):
        await download_order_photo(bot, message, tmp_path, max_file_mb=20)
