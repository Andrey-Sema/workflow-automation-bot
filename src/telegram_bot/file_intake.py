"""Downloads user-submitted photos/documents to disk.

Deliberately never uses the sender-supplied file name: the destination is
always a fresh `uuid4` + an extension chosen from a small allow-list of
mime types, which rules out path traversal or any funny-business via a
malicious "file_name" (Telegram lets a client set that to anything).
"""

from __future__ import annotations

import uuid
from pathlib import Path

from aiogram import Bot
from aiogram.types import Message

from src.errors import FileIntakeError

ALLOWED_DOCUMENT_MIME = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}


async def download_order_photo(bot: Bot, message: Message, destination_dir: Path, max_file_mb: int) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    max_bytes = max_file_mb * 1024 * 1024

    if message.photo:
        photo = message.photo[-1]
        if photo.file_size and photo.file_size > max_bytes:
            size_mb = photo.file_size / 1024 / 1024
            raise FileIntakeError(f"Фото слишком большое: {size_mb:.1f} МБ (лимит {max_file_mb} МБ)")
        dest = destination_dir / f"{uuid.uuid4().hex}.jpg"
        await bot.download(photo.file_id, destination=dest)
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
        dest = destination_dir / f"{uuid.uuid4().hex}{ext}"
        await bot.download(doc.file_id, destination=dest)
        return dest

    raise FileIntakeError("Пришли фото бланка или PDF-файл.")
