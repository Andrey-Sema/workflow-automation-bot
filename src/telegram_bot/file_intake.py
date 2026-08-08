"""Downloads user-submitted photos/documents to disk.

Deliberately never uses the sender-supplied file name: the destination is
always a fresh `uuid4` + an extension chosen from a small allow-list of
mime types, which rules out path traversal or any funny-business via a
malicious "file_name" (Telegram lets a client set that to anything).

The same distrust applies to the other two things the client controls:

* **`mime_type` is a claim, not a fact.** It is whatever the sending client
  put in the upload, so an allow-list on it alone decides nothing about the
  bytes that arrive. Downstream those bytes go to Pillow / PyMuPDF — image
  and PDF parsers written in C, and the exact place where a malformed file
  turns into a memory-safety bug. So the content is sniffed after download
  and anything whose magic bytes don't match a format we actually handle is
  deleted before it can reach a parser.

* **`file_size` may be absent.** The pre-download check is skipped entirely
  when Telegram doesn't populate it, so the size is enforced again on the
  bytes that actually landed on disk. Without that, "no declared size"
  meant "no limit" — an unbounded write into the data volume.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from aiogram import Bot
from aiogram.types import Message

from src.errors import FileIntakeError

logger = logging.getLogger(__name__)

ALLOWED_DOCUMENT_MIME = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}

# Magic-byte prefixes for the formats the pipeline can actually read.
# Checked against the downloaded bytes, never against the declared type.
_MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", ".jpg"),           # JPEG
    (b"\x89PNG\r\n\x1a\n", ".png"),      # PNG
    (b"%PDF-", ".pdf"),                  # PDF
)

_MAGIC_PREFIX_LEN = max(len(sig) for sig, _ in _MAGIC_SIGNATURES)


def _sniff_format(path: Path) -> str | None:
    """Returns the extension implied by the file's magic bytes, or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(_MAGIC_PREFIX_LEN)
    except OSError:
        return None
    return next((ext for sig, ext in _MAGIC_SIGNATURES if head.startswith(sig)), None)


def _discard(path: Path) -> None:
    """Removes a rejected upload. Best-effort: a failure to delete must not
    mask the validation error that caused the rejection."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Не удалось удалить отклонённый файл %s", path.name)


def _enforce_downloaded_file(path: Path, max_bytes: int, max_file_mb: int) -> None:
    actual = path.stat().st_size
    if actual > max_bytes:
        _discard(path)
        raise FileIntakeError(
            f"Файл слишком большой: {actual / 1024 / 1024:.1f} МБ (лимит {max_file_mb} МБ)"
        )
    if actual == 0:
        _discard(path)
        raise FileIntakeError("Файл пустой — пришли фото бланка ещё раз.")

    sniffed = _sniff_format(path)
    if sniffed is None:
        _discard(path)
        logger.warning("Отклонён файл %s: содержимое не JPEG/PNG/PDF", path.name)
        raise FileIntakeError(
            "Содержимое файла не похоже на фото или PDF. Пришли фото бланка или PDF-файл."
        )


async def download_order_photo(bot: Bot, message: Message, destination_dir: Path, max_file_mb: int) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    max_bytes = max_file_mb * 1024 * 1024

    if message.photo:
        photo = message.photo[-1]
        if photo.file_size and photo.file_size > max_bytes:
            size_mb = photo.file_size / 1024 / 1024
            raise FileIntakeError(f"Фото слишком большое: {size_mb:.1f} МБ (лимит {max_file_mb} МБ)")
        dest = _unique_path(destination_dir, ".jpg")
        await bot.download(photo.file_id, destination=dest)
        _enforce_downloaded_file(dest, max_bytes, max_file_mb)
        return dest

    if message.document:
        doc = message.document
        ext = ALLOWED_DOCUMENT_MIME.get(doc.mime_type or "")
        if not ext:
            raise FileIntakeError(
                f"Формат файла не поддерживается: {doc.mime_type or 'неизвестен'}. Пришли фото или PDF."
            )
        if doc.file_size and doc.file_size > max_bytes:
            size_mb = doc.file_size / 1024 / 1024
            raise FileIntakeError(f"Файл слишком большой: {size_mb:.1f} МБ (лимит {max_file_mb} МБ)")
        dest = _unique_path(destination_dir, ext)
        await bot.download(doc.file_id, destination=dest)
        _enforce_downloaded_file(dest, max_bytes, max_file_mb)
        return dest

    raise FileIntakeError("Пришли фото бланка или PDF-файл.")


def _unique_path(destination_dir: Path, ext: str) -> Path:
    return destination_dir / f"{uuid.uuid4().hex}{ext}"
