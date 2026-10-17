from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import UploadFile
from PIL import Image

from app.services import media_service as media_service_module
from app.services.media_service import ImageValidationError, MediaService


def _png_bytes(size: tuple[int, int]) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color="blue").save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def media_service(monkeypatch, tmp_path) -> MediaService:
    monkeypatch.setattr(
        media_service_module,
        "get_settings",
        lambda: SimpleNamespace(
            media_root=str(tmp_path),
            media_base_url="/media",
            media_public_url=None,
            max_image_upload_bytes=5 * 1024 * 1024,
            image_max_width=640,
            image_max_height=480,
            max_pdf_upload_bytes=30 * 1024 * 1024,
        ),
    )
    return MediaService(base_path=tmp_path)


@pytest.mark.asyncio
async def test_save_image_resizes_before_persisting(media_service: MediaService) -> None:
    upload = UploadFile(filename="original.png", file=BytesIO(_png_bytes((3000, 1500))))

    url = await media_service.save_image(
        upload,
        category="avatars",
        identifier="user_1",
        filename="avatar.png",
    )

    saved_path = media_service.base_path / "avatars" / "avatar.png"
    with Image.open(saved_path) as saved:
        assert saved.size == (640, 320)
    assert url == "/media/avatars/avatar.png"


@pytest.mark.asyncio
async def test_save_content_image_resizes_before_persisting(
    media_service: MediaService,
) -> None:
    upload = UploadFile(filename="original.png", file=BytesIO(_png_bytes((3000, 1500))))

    url = await media_service.save_content_image(
        upload,
        content_type="entity",
        content_id="7",
        is_main=True,
    )

    saved_path = media_service.base_path / "entity" / "7" / "file.png"
    with Image.open(saved_path) as saved:
        assert saved.size == (640, 320)
    assert url == "/media/entity/7/file.png"


@pytest.mark.asyncio
async def test_save_content_image_respects_flow_specific_dimensions(
    media_service: MediaService,
) -> None:
    upload = UploadFile(filename="cover.png", file=BytesIO(_png_bytes((3000, 1500))))

    await media_service.save_content_image(
        upload,
        content_type="library",
        content_id="1/2",
        is_main=True,
        resize=(800, 1200),
    )

    saved_path = media_service.base_path / "library" / "1" / "2" / "file.png"
    with Image.open(saved_path) as saved:
        assert saved.size == (640, 320)


@pytest.mark.asyncio
async def test_save_image_never_exceeds_policy_with_legacy_configured_limits(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        media_service_module,
        "get_settings",
        lambda: SimpleNamespace(
            media_root=str(tmp_path),
            media_base_url="/media",
            media_public_url=None,
            max_image_upload_bytes=5 * 1024 * 1024,
            image_max_width=4096,
            image_max_height=4096,
            max_pdf_upload_bytes=30 * 1024 * 1024,
        ),
    )
    service = MediaService(base_path=tmp_path)
    upload = UploadFile(filename="portrait.png", file=BytesIO(_png_bytes((1000, 2000))))

    await service.save_image(
        upload,
        category="avatars",
        identifier="user_1",
        filename="portrait.png",
    )

    with Image.open(tmp_path / "avatars" / "portrait.png") as saved:
        assert saved.size == (240, 480)


@pytest.mark.asyncio
async def test_save_image_still_rejects_uploads_over_byte_limit(
    media_service: MediaService,
) -> None:
    upload = UploadFile(
        filename="oversized.png",
        file=BytesIO(b"x" * (5 * 1024 * 1024 + 1)),
    )

    with pytest.raises(ImageValidationError, match="exceeds size limit"):
        await media_service.save_image(
            upload,
            category="avatars",
            identifier="user_1",
            filename="avatar.png",
        )
