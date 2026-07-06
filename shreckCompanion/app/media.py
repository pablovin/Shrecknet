from __future__ import annotations

import imghdr
import re
from io import BytesIO
from pathlib import Path

from fastapi import UploadFile
from PIL import Image

from app.core.config import Settings


class ImageValidationError(ValueError):
    pass


class CompanionMediaService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.media_root = settings.media_path
        self.media_root.mkdir(parents=True, exist_ok=True)

    async def save_companion_avatar(self, upload: UploadFile, *, username: str, user_id: int) -> str:
        contents = await upload.read()
        if not contents:
            raise ImageValidationError("Uploaded image is empty")
        if len(contents) > self.settings.max_image_upload_bytes:
            raise ImageValidationError("Uploaded image exceeds size limit")
        if imghdr.what(None, h=contents) not in {"png", "jpeg", "gif", "bmp", "webp"}:
            raise ImageValidationError("Unsupported image type")

        image = Image.open(BytesIO(contents))
        image = image.convert("RGBA" if image.mode in ("RGBA", "P") else "RGB")
        image.thumbnail((self.settings.image_max_width, self.settings.image_max_height))

        safe_username = self._safe_username(username, user_id)
        relative_path = Path(safe_username) / "companion.png"
        absolute_path = self.media_root / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)

        temp_path = absolute_path.with_suffix(".png.tmp")
        image.save(temp_path, format="PNG", optimize=True)
        temp_path.replace(absolute_path)
        return f"{self.settings.media_base_url.rstrip('/')}/{relative_path.as_posix()}"

    @staticmethod
    def _safe_username(username: str | None, user_id: int) -> str:
        value = (username or "").strip().lower()
        value = re.sub(r"[^a-z0-9_-]+", "-", value).strip("-")
        return value or f"user-{user_id}"
