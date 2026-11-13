"""Source-owned immutable file assets; never expose raw file paths to callers."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
import os

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.sources import SourceAsset
from app.schemas.sources import AuthoredContent, SourceSection


def asset_id(source_id: str, section_id: str, kind: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"shreckworld-source-asset:{source_id}:{section_id}:{kind}"))


def markdown_bytes(section: SourceSection) -> bytes:
    ref = section.content_reference
    if not isinstance(ref, AuthoredContent):
        raise ValueError("Only authored source sections support Markdown assets")
    return ("\n\n".join(p.text.rstrip() for p in ref.paragraphs if p.text.strip()).rstrip() + "\n").encode("utf-8")


def stage_asset(source_id: str, identifier: str, kind: str, data: bytes, extension: str) -> tuple[Path, str, bool]:
    if kind not in {"markdown", "image"} or extension not in {"md", "png", "jpg", "webp"}:
        raise ValueError("Unsupported asset kind or extension")
    digest = sha256(data).hexdigest()
    root = (get_settings().media_root / "ontologies" / source_id / "assets" / identifier).resolve()
    media = get_settings().media_root.resolve()
    if media not in root.parents:
        raise ValueError("Asset storage escaped protected media")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{digest}.{extension}"
    if path.is_file():
        return path, digest, False
    try:
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        return path, digest, False
    return path, digest, True


def read_asset(db: Session, source_id: str, identifier: str) -> SourceAsset:
    asset = db.get(SourceAsset, identifier)
    if asset is None or asset.source_id != source_id:
        raise HTTPException(404, "Source asset not found")
    from app.services.source_workspace import get_organization
    from app.models import ShreckWorldLibraryItem
    item = db.get(ShreckWorldLibraryItem, source_id)
    if item is None:
        raise HTTPException(404, "Source not found")
    organization = get_organization(db, item)
    section = next((node for node in organization.nodes if node.id == asset.section_id), None)
    if section is None:
        raise HTTPException(404, "Source asset is no longer referenced")
    linked = section.metadata.markdown_asset_id if asset.kind == "markdown" else section.metadata.image_asset_id
    if linked != identifier:
        raise HTTPException(404, "Source asset is no longer referenced")
    path = Path(asset.storage_path).resolve()
    root = (get_settings().media_root / "ontologies" / source_id).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "Source asset file unavailable")
    return asset


def validate_asset_references(db: Session, source_id: str, nodes: list[SourceSection]) -> None:
    for section in nodes:
        for kind, identifier in (("markdown", section.metadata.markdown_asset_id),
                                 ("image", section.metadata.image_asset_id)):
            if identifier is None:
                continue
            asset = db.get(SourceAsset, identifier)
            if asset is None or (asset.source_id, asset.section_id, asset.kind) != (source_id, section.id, kind):
                raise HTTPException(422, "Source section asset must belong to this section")


def update_markdown_assets(db: Session, source_id: str, nodes: list[SourceSection]) -> list[Path]:
    """Stage changed immutable Markdown revisions; SQL transaction is owned by caller."""
    staged: list[Path] = []
    for node in nodes:
        identifier = node.metadata.markdown_asset_id
        if identifier is None:
            continue
        asset = db.get(SourceAsset, identifier)
        if asset is None or asset.source_id != source_id or asset.section_id != node.id or asset.kind != "markdown":
            raise HTTPException(422, "Invalid Markdown asset reference")
        content = markdown_bytes(node)
        digest = sha256(content).hexdigest()
        if digest == asset.content_sha256:
            continue
        try:
            path, _, created = stage_asset(source_id, identifier, "markdown", content, "md")
        except OSError as exc:
            for staged_path in staged:
                staged_path.unlink(missing_ok=True)
            raise HTTPException(500, "Unable to store section Markdown revision") from exc
        if created:
            staged.append(path)
        asset.storage_path = str(path)
        asset.content_sha256 = digest
    return staged
