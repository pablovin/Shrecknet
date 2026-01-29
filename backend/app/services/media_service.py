from __future__ import annotations

import imghdr
import os
import re
import secrets
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from PIL import Image

from app.core.config_store import get_settings


class ImageValidationError(ValueError):
    pass


class PdfValidationError(ValueError):
    """Raised when a PDF upload fails validation."""


class MediaService:
    def __init__(self, *, base_path: Path | None = None) -> None:
        settings = get_settings()
        self.base_path = base_path or Path(settings.media_root)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.max_size = settings.max_image_upload_bytes
        self.max_width = settings.image_max_width
        self.max_height = settings.image_max_height
        self.max_pdf_bytes = settings.max_pdf_upload_bytes

    async def save_image(
        self,
        upload: UploadFile,
        *,
        category: str,
        identifier: str,
        resize: tuple[int, int] | None = None,
        metadata: dict[str, Any] | None = None,
        filename: str | None = None,
    ) -> str:
        contents = await self._read_limited(upload)
        self._validate_format(contents)

        image = Image.open(BytesIO(contents))
        image = image.convert("RGBA" if image.mode in ("RGBA", "P") else "RGB")

        target_size = resize or (self.max_width, self.max_height)
        if target_size:
            max_width, max_height = target_size
            width, height = image.size
            if width > max_width or height > max_height:
                ratio = min(max_width / width, max_height / height)
                new_size = (int(width * ratio), int(height * ratio))
                if hasattr(Image, "Resampling"):
                    image = image.resize(new_size, Image.Resampling.LANCZOS)
                else:  # pragma: no cover - older Pillow fallback
                    image = image.resize(new_size, Image.ANTIALIAS)  # type: ignore[attr-defined]

        resolved_filename = (
            self._sanitize_filename(filename)
            if filename
            else self._build_filename(upload.filename, identifier)
        )
        relative_path = Path(category) / resolved_filename
        absolute_path = self.base_path / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)

        if absolute_path.exists():
            try:
                absolute_path.unlink()
            except OSError:
                # If removal fails, attempt to overwrite directly
                pass

        image_format = self._determine_format(image, contents, resolved_filename)
        image.save(absolute_path, format=image_format, optimize=True)

        return self._build_url(relative_path)

    async def _read_limited(self, upload: UploadFile) -> bytes:
        data = await upload.read()
        if len(data) > self.max_size:
            raise ImageValidationError("Uploaded file exceeds size limit")
        return data

    def _validate_format(self, contents: bytes) -> None:
        detected = imghdr.what(None, h=contents)
        if detected not in {"png", "jpeg", "gif", "bmp", "webp"}:
            raise ImageValidationError("Unsupported image type")

    def _guess_format(self, contents: bytes) -> str | None:
        detected = imghdr.what(None, h=contents)
        if detected is None:
            return None
        return detected.upper()

    def _build_filename(self, original_name: str | None, identifier: str) -> str:
        safe_identifier = self._sanitize_identifier(identifier)
        ext = self._extract_extension(original_name) or "png"
        token = secrets.token_urlsafe(8)
        return f"{safe_identifier}_{token}.{ext}"

    def _sanitize_identifier(self, identifier: str) -> str:
        identifier = identifier.strip().lower()
        identifier = identifier.replace("..", "")
        return re.sub(r"[^a-z0-9_-]+", "-", identifier) or "asset"

    def _sanitize_filename(self, filename: str) -> str:
        cleaned = Path(filename).name
        cleaned = cleaned.replace("..", "")
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", cleaned)
        cleaned = cleaned.strip(".-")
        if not cleaned:
            return "asset.png"
        return cleaned

    def _determine_format(
        self, image: Image.Image, contents: bytes, filename: str
    ) -> str:
        ext = Path(filename).suffix.lower().lstrip(".")
        if ext in {"jpg", "jpeg"}:
            return "JPEG"
        if ext == "png":
            return "PNG"
        if ext in {"gif", "bmp", "webp"}:
            return ext.upper()

        inferred = image.format or self._guess_format(contents) or "PNG"
        return "JPEG" if inferred.upper() == "JPG" else inferred

    def _extract_extension(self, original_name: str | None) -> str | None:
        if not original_name:
            return None
        _, ext = os.path.splitext(original_name)
        ext = ext.lower().strip(".")
        if ext in {"jpg", "jpeg", "png", "gif", "bmp", "webp"}:
            return "jpg" if ext == "jpeg" else ext
        return None

    def _build_url(self, relative_path: Path) -> str:
        relative_str = relative_path.as_posix()
        public_base = getattr(settings, "media_public_url", None)
        if public_base:
            public_base = public_base.rstrip("/")
            return f"{public_base}/{relative_str}"
        base_url = settings.media_base_url.rstrip("/")
        return f"{base_url}/{relative_str}"

    async def save_content_image(
        self,
        upload: UploadFile,
        *,
        content_type: str,
        content_id: str,
        is_main: bool = False,
        resize: tuple[int, int] | None = None,
    ) -> str:
        """
        Save an image for a specific content type and ID.

        Args:
            upload: The file to upload
            content_type: String identifying the content type (e.g., 'user', 'avatar', 'post')
            content_id: String identifying the specific content instance
            is_main: If True, saves as 'file.png' (overwrites); if False, uses incremental naming
            resize: Optional resize dimensions

        Returns:
            The URL path to the saved image
        """
        contents = await self._read_limited(upload)
        self._validate_format(contents)

        image = Image.open(BytesIO(contents))
        image = image.convert("RGBA" if image.mode in ("RGBA", "P") else "RGB")

        target_size = resize or (self.max_width, self.max_height)
        if target_size:
            max_width, max_height = target_size
            width, height = image.size
            if width > max_width or height > max_height:
                ratio = min(max_width / width, max_height / height)
                new_size = (int(width * ratio), int(height * ratio))
                if hasattr(Image, "Resampling"):
                    image = image.resize(new_size, Image.Resampling.LANCZOS)
                else:  # pragma: no cover - older Pillow fallback
                    image = image.resize(new_size, Image.ANTIALIAS)  # type: ignore[attr-defined]

        # Create directory path: media/content_type/content_id/
        folder_path = self.base_path / content_type / content_id
        folder_path.mkdir(parents=True, exist_ok=True)

        # Determine filename
        if is_main:
            # Main file: always 'file.png', overwrites existing
            filename = "file.png"
            file_path = folder_path / filename

            # Remove existing file if it exists
            if file_path.exists():
                try:
                    file_path.unlink()
                except OSError:
                    # If removal fails, will overwrite directly
                    pass
        else:
            # Non-main file: use incremental ID
            existing_files = list(folder_path.glob("*.png"))
            # Extract numeric IDs from existing files (excluding 'file.png')
            existing_ids = []
            for f in existing_files:
                if f.name != "file.png":
                    try:
                        file_id = int(f.stem)
                        existing_ids.append(file_id)
                    except ValueError:
                        # Skip non-numeric filenames
                        continue

            # Get next ID
            next_id = max(existing_ids, default=0) + 1
            filename = f"{next_id}.png"
            file_path = folder_path / filename

        # Save the image
        image_format = "PNG"
        image.save(file_path, format=image_format, optimize=True)

        # Build relative path for URL
        relative_path = Path(content_type) / content_id / filename
        return self._build_url(relative_path)

    async def save_content_pdf(
        self,
        upload: UploadFile,
        *,
        content_id: str,
    ) -> str:
        """
        Persist a PDF for a specific content item, preserving the original filename.

        Raises:
            PdfValidationError: If the upload does not look like a PDF or exceeds limits.
        """
        if not upload.filename:
            raise PdfValidationError("PDF file must include a filename")

        sanitized_filename = self._sanitize_pdf_filename(upload.filename)
        content_type = (upload.content_type or "").lower()
        if "pdf" not in content_type and not sanitized_filename.lower().endswith(".pdf"):
            raise PdfValidationError("Uploaded file must be a PDF")

        folder_path = self.base_path / "content" / content_id
        folder_path.mkdir(parents=True, exist_ok=True)

        file_path = folder_path / sanitized_filename
        temp_path = file_path.with_suffix(file_path.suffix + ".tmp")

        total = 0
        chunk_size = 1_048_576  # 1 MB
        first_chunk = True
        try:
            with temp_path.open("wb") as buffer:
                while True:
                    chunk = await upload.read(chunk_size)
                    if not chunk:
                        break
                    if first_chunk:
                        if not chunk.lstrip().startswith(b"%PDF"):
                            raise PdfValidationError("Uploaded file must be a valid PDF document")
                        first_chunk = False
                    total += len(chunk)
                    if total > self.max_pdf_bytes:
                        raise PdfValidationError("Uploaded PDF exceeds size limit")
                    buffer.write(chunk)

            if total == 0:
                raise PdfValidationError("Uploaded PDF file is empty")

            temp_path.replace(file_path)
        except Exception:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise
        finally:
            await upload.seek(0)

        relative_path = Path("content") / content_id / sanitized_filename
        return self._build_url(relative_path)

    def _sanitize_pdf_filename(self, filename: str) -> str:
        cleaned = Path(filename).name
        cleaned = cleaned.replace("..", "")
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", cleaned)
        cleaned = cleaned.strip()
        if not cleaned:
            return "document.pdf"
        if not cleaned.lower().endswith(".pdf"):
            cleaned = cleaned.rstrip(".-")
            if not cleaned:
                cleaned = "document"
            cleaned = f"{cleaned}.pdf"
        return cleaned
