from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.errors import FileIntakeError
from src.telegram_bot.file_intake import download_order_photo

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"\x00" * 64
# What an attacker actually gets past the mime allow-list: any bytes at all,
# labelled image/png. Here, a Windows executable.
EXE = b"MZ\x90\x00" + b"\x00" * 64


def fake_bot(payload: bytes = JPEG):
    """Writes real bytes at the destination, like the live Bot.download does.

    The intake code now inspects what actually landed on disk, so a mock
    that only records the call can't exercise it.
    """
    bot = SimpleNamespace()

    async def download(file_id, destination):
        destination.write_bytes(payload)

    bot.download = AsyncMock(side_effect=download)
    return bot


async def test_downloads_photo_with_generated_filename(tmp_path):
    bot = fake_bot(JPEG)
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
    bot = fake_bot(PDF)
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
    bot = fake_bot(PNG)
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


# --- содержимое важнее заявленного mime_type ---

async def test_executable_disguised_as_png_is_rejected_and_deleted(tmp_path):
    """mime_type задаёт клиент, а байты уходят в Pillow/PyMuPDF — парсеры на
    C. Файл, который не является ни картинкой, ни PDF, до них доходить не
    должен и не должен оставаться на диске."""
    bot = fake_bot(EXE)
    message = SimpleNamespace(
        photo=None,
        document=SimpleNamespace(file_id="d1", file_size=1000, mime_type="image/png"),
    )

    with pytest.raises(FileIntakeError, match="не похоже"):
        await download_order_photo(bot, message, tmp_path, max_file_mb=20)
    assert list(tmp_path.iterdir()) == []


async def test_photo_that_is_not_an_image_is_rejected(tmp_path):
    bot = fake_bot(EXE)
    message = SimpleNamespace(photo=[SimpleNamespace(file_id="f1", file_size=1000)], document=None)

    with pytest.raises(FileIntakeError):
        await download_order_photo(bot, message, tmp_path, max_file_mb=20)
    assert list(tmp_path.iterdir()) == []


async def test_pdf_declared_as_jpeg_is_still_accepted(tmp_path):
    """Несовпадение mime и содержимого само по себе не атака — важно лишь,
    что байты относятся к формату, который пайплайн умеет читать."""
    bot = fake_bot(PDF)
    message = SimpleNamespace(
        photo=None,
        document=SimpleNamespace(file_id="d1", file_size=1000, mime_type="image/jpeg"),
    )
    path = await download_order_photo(bot, message, tmp_path, max_file_mb=20)
    assert path.exists()


# --- размер проверяется по факту, а не по заявке ---

async def test_oversized_file_without_declared_size_is_rejected_after_download(tmp_path):
    """file_size у Telegram необязательный. Раньше его отсутствие означало
    «проверку не делаем» — то есть запись на диск без ограничения."""
    bot = fake_bot(JPEG + b"\x00" * (2 * 1024 * 1024))
    message = SimpleNamespace(
        photo=None,
        document=SimpleNamespace(file_id="d1", file_size=None, mime_type="image/jpeg"),
    )

    with pytest.raises(FileIntakeError, match="слишком большой"):
        await download_order_photo(bot, message, tmp_path, max_file_mb=1)
    assert list(tmp_path.iterdir()) == []


async def test_empty_file_is_rejected(tmp_path):
    bot = fake_bot(b"")
    message = SimpleNamespace(photo=[SimpleNamespace(file_id="f1", file_size=None)], document=None)

    with pytest.raises(FileIntakeError, match="пустой"):
        await download_order_photo(bot, message, tmp_path, max_file_mb=20)
    assert list(tmp_path.iterdir()) == []
