from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from app.core import config_store
from app.core.config_store import update_settings
from app.services.media_service import MediaService
from app.services.setup_service import SetupService


class _DummyOntologyRepository:
    async def get_by_name(self, _name: str):
        return None


class _DummyOntologyService:
    def __init__(self) -> None:
        self.repository = _DummyOntologyRepository()


def _reset_settings_cache() -> None:
    config_store._settings_cache = None


def test_resolve_default_images_root_supports_package_local_layout() -> None:
    service = SetupService(
        ontology_service=_DummyOntologyService(),
        media_service=None,
    )

    expected = (
        Path(__file__).resolve().parents[1] / "default" / "images" / "world"
    )
    assert service.default_images_root == expected


@pytest.mark.asyncio
async def test_copy_default_world_images_writes_expected_media_paths(
    monkeypatch, tmp_path
) -> None:
    media_root = tmp_path / "media"
    source_root = tmp_path / "defaults"
    source_root.mkdir()

    def write_png(path: Path, size: tuple[int, int]) -> None:
        image = Image.new("RGB", size, color="red")
        image.save(path, format="PNG")

    write_png(source_root / "World.png", (2500, 1250))
    write_png(source_root / "Adventure.png", (2500, 1250))

    monkeypatch.setenv("SHRECKNET_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SHRECKNET_MEDIA_BASE_URL", "/media")
    monkeypatch.delenv("SHRECKNET_MEDIA_PUBLIC_URL", raising=False)
    _reset_settings_cache()
    update_settings(
        {
            "media_root": str(media_root),
            "media_base_url": "/media",
            "media_public_url": None,
        }
    )

    service = SetupService(
        ontology_service=_DummyOntologyService(),
        media_service=MediaService(base_path=media_root),
        default_images_root=source_root,
    )

    ontology_url = await service._copy_ontology_image(42)
    entity_url = await service._copy_entity_image(7, "Adventure.png")

    assert ontology_url == "/media/ontology/42/file.png"
    assert entity_url == "/media/entity/7/file.png"
    with Image.open(media_root / "ontology" / "42" / "file.png") as image:
        assert image.size == (640, 320)
    with Image.open(media_root / "entity" / "7" / "file.png") as image:
        assert image.size == (640, 320)

    _reset_settings_cache()
