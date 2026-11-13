"""Persist world-local hierarchy collections while leaving source documents untouched."""
from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ShreckWorldLibraryItem, SourceCollection, SourceHierarchy
from app.repositories.source_scope import OntologyScope
from app.schemas.source_collections import CollectionCreate, CollectionEntry, CollectionRead, CollectionUpdate
from app.services.source_workspace import latest_hierarchy


def fail(status: int, detail: str):
    raise HTTPException(status_code=status, detail=detail)


def scoped_collection(db: Session, scope: OntologyScope, collection_id: str) -> SourceCollection:
    collection = db.get(SourceCollection, collection_id)
    if collection is None or collection.ontology_id != scope.ontology_id:
        fail(404, "Hierarchy collection not found in this ontology")
    return collection


def template_for(db: Session, collection: SourceCollection) -> SourceHierarchy:
    template = db.get(SourceHierarchy, (
        collection.hierarchy_template_id, collection.hierarchy_template_version,
    ))
    if template is None:
        fail(409, "Pinned hierarchy version is unavailable")
    return template


def read_collection(db: Session, collection: SourceCollection) -> CollectionRead:
    template = template_for(db, collection)
    return CollectionRead(
        id=collection.id, ontology_id=collection.ontology_id,
        source_type=collection.source_type,
        hierarchy_template_id=collection.hierarchy_template_id,
        hierarchy_template_version=collection.hierarchy_template_version,
        section_types=list(template.section_types),
        template_has_image=bool(template.image_path),
        name=collection.name, description=collection.description or "",
        archived=collection.archived, revision=collection.revision,
        nodes=[CollectionEntry.model_validate(node) for node in collection.nodes or []],
    )


def list_collections(db: Session, scope: OntologyScope, category: str) -> list[CollectionRead]:
    items = db.scalars(select(SourceCollection).where(
        SourceCollection.ontology_id == scope.ontology_id,
        SourceCollection.source_type == category,
    ).order_by(SourceCollection.name, SourceCollection.id)).all()
    return [read_collection(db, item) for item in items]


def create_collection(db: Session, scope: OntologyScope, payload: CollectionCreate) -> CollectionRead:
    template = db.get(SourceHierarchy, (
        payload.hierarchy_template_id, payload.hierarchy_template_version,
    ))
    if template is None or template.archived:
        fail(422, "Select an active hierarchy template version")
    # A template has one assignment per category/world. Re-adding an archived
    # assignment restores its entries; no duplicate hierarchy gallery cards.
    existing = db.scalar(select(SourceCollection).where(
        SourceCollection.ontology_id == scope.ontology_id,
        SourceCollection.source_type == payload.source_type,
        SourceCollection.hierarchy_template_id == payload.hierarchy_template_id,
    ))
    if existing:
        if not existing.archived:
            fail(409, "This hierarchy is already assigned to the category")
        existing.archived = False
        existing.revision += 1
        db.commit()
        return read_collection(db, existing)
    record = SourceCollection(
        id=str(uuid4()), ontology_id=scope.ontology_id, source_type=payload.source_type,
        hierarchy_template_id=template.id, hierarchy_template_version=template.version,
        name=payload.name or template.name, description=template.description or "",
        nodes=[], revision=1, archived=False,
    )
    try:
        db.add(record)
        db.commit()
    except IntegrityError:
        db.rollback()
        fail(409, "Hierarchy assignment already exists; reload to review")
    return read_collection(db, record)


def _validate_entries(db: Session, collection: SourceCollection, entries: list[CollectionEntry]) -> None:
    template = template_for(db, collection)
    by_id = {node.id: node for node in entries}
    previous = {node["id"]: CollectionEntry.model_validate(node) for node in (collection.nodes or [])}
    source_ids: set[str] = set()
    for node in entries:
        if node.level >= len(template.section_types):
            fail(422, "Entry level exceeds the pinned hierarchy template")
        if node.parent_id is None:
            if node.level != 0:
                fail(422, "Root entries must be at the first level")
        else:
            parent = by_id.get(node.parent_id)
            if parent is None or node.level != parent.level + 1:
                fail(422, "Entries must belong to the next hierarchy level")
        old = previous.get(node.id)
        if node.image_key != (old.image_key if old else None):
            fail(422, "Entry images can only change through the image upload endpoint")
        source_ids.update(node.source_ids)
    if source_ids:
        found = list(db.scalars(select(ShreckWorldLibraryItem).where(
            ShreckWorldLibraryItem.id.in_(source_ids),
            ShreckWorldLibraryItem.ontology_id == collection.ontology_id,
            ShreckWorldLibraryItem.source_type == collection.source_type,
        )))
        if {item.id for item in found} != source_ids:
            fail(422, "All attached documents must belong to this world and source category")


def save_collection(
    db: Session, scope: OntologyScope, collection_id: str, payload: CollectionUpdate,
) -> CollectionRead:
    record = scoped_collection(db, scope, collection_id)
    if payload.expected_revision != record.revision:
        fail(409, "Hierarchy was updated elsewhere. Reload before saving")
    _validate_entries(db, record, payload.nodes)
    result = db.execute(update(SourceCollection).where(
        SourceCollection.id == record.id,
        SourceCollection.ontology_id == scope.ontology_id,
        SourceCollection.revision == payload.expected_revision,
    ).values(
        name=payload.name, description=payload.description,
        archived=payload.archived, nodes=[node.model_dump(mode="json") for node in payload.nodes],
        revision=record.revision + 1,
    ))
    if result.rowcount != 1:
        db.rollback()
        fail(409, "Hierarchy was updated elsewhere. Reload before saving")
    db.commit()
    db.refresh(record)
    return read_collection(db, record)


def remove_collection(db: Session, scope: OntologyScope, collection_id: str, expected_revision: int) -> None:
    record = scoped_collection(db, scope, collection_id)
    result = db.execute(update(SourceCollection).where(
        SourceCollection.id == record.id, SourceCollection.ontology_id == scope.ontology_id,
        SourceCollection.revision == expected_revision,
    ).values(archived=True, revision=expected_revision + 1))
    if result.rowcount != 1:
        db.rollback()
        fail(409, "Hierarchy changed; reload before removing it")
    db.commit()


def _image_root(collection: SourceCollection, entry_id: str) -> Path:
    return get_settings().media_root / "source-collections" / str(collection.ontology_id) / collection.id / entry_id


def upload_entry_image(
    db: Session, scope: OntologyScope, collection_id: str, entry_id: str,
    expected_revision: int, raw: bytes, content_type: str | None,
) -> CollectionRead:
    record = scoped_collection(db, scope, collection_id)
    if expected_revision != record.revision:
        fail(409, "Hierarchy changed; reload before uploading the image")
    if len(raw) == 0 or len(raw) > 5 * 1024 * 1024:
        fail(413, "Entry artwork must be between 1 byte and 5 MB")
    formats = {
        "image/png": (raw.startswith(bytes.fromhex("89504e470d0a1a0a")), "png"),
        "image/jpeg": (raw.startswith(bytes.fromhex("ffd8ff")) and raw.endswith(bytes.fromhex("ffd9")), "jpg"),
        "image/webp": (raw.startswith(b"RIFF") and raw[8:12] == b"WEBP", "webp"),
    }
    valid, extension = formats.get(content_type or "", (False, ""))
    if not valid:
        fail(415, "Supported artwork: PNG, JPEG, WebP")
    nodes = [CollectionEntry.model_validate(node) for node in record.nodes or []]
    node = next((item for item in nodes if item.id == entry_id), None)
    if node is None:
        fail(404, "Entry not found in this hierarchy")
    key = f"{uuid4().hex}.{extension}"
    target = _image_root(record, entry_id) / key
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("xb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        revised = [item.model_copy(update={"image_key": key}) if item.id == entry_id else item for item in nodes]
        result = db.execute(update(SourceCollection).where(
            SourceCollection.id == record.id, SourceCollection.ontology_id == scope.ontology_id,
            SourceCollection.revision == expected_revision,
        ).values(nodes=[item.model_dump(mode="json") for item in revised], revision=expected_revision + 1))
        if result.rowcount != 1:
            db.rollback()
            fail(409, "Hierarchy changed during artwork upload")
        db.commit()
    except Exception:
        db.rollback()
        target.unlink(missing_ok=True)
        raise
    db.refresh(record)
    return read_collection(db, record)


def remove_entry_image(
    db: Session, scope: OntologyScope, collection_id: str, entry_id: str,
    expected_revision: int,
) -> CollectionRead:
    """Detach artwork without erasing historical on-disk revisions."""
    record = scoped_collection(db, scope, collection_id)
    nodes = [CollectionEntry.model_validate(node) for node in record.nodes or []]
    if not any(node.id == entry_id for node in nodes):
        fail(404, "Entry not found")
    revised = [
        node.model_copy(update={"image_key": None}) if node.id == entry_id else node
        for node in nodes
    ]
    result = db.execute(update(SourceCollection).where(
        SourceCollection.id == record.id, SourceCollection.ontology_id == scope.ontology_id,
        SourceCollection.revision == expected_revision,
    ).values(nodes=[node.model_dump(mode="json") for node in revised],
             revision=expected_revision + 1))
    if result.rowcount != 1:
        db.rollback()
        fail(409, "Hierarchy changed during artwork removal")
    db.commit()
    db.refresh(record)
    return read_collection(db, record)


def entry_image(db: Session, scope: OntologyScope, collection_id: str, entry_id: str) -> Path:
    record = scoped_collection(db, scope, collection_id)
    node = next((item for item in record.nodes or [] if item["id"] == entry_id), None)
    if not node or not node.get("image_key"):
        fail(404, "Entry image not found")
    key = node["image_key"]
    if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{32}\.(png|jpg|webp)", key):
        fail(404, "Entry artwork unavailable")
    path = (_image_root(record, entry_id) / key).resolve()
    media = get_settings().media_root.resolve()
    if media not in path.parents or not path.is_file():
        fail(404, "Entry artwork unavailable")
    return path
