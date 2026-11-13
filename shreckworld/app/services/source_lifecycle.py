"""Remove canonical sources and their structured document projections."""
import logging
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ShreckWorld, ShreckWorldDocument, ShreckWorldLibraryItem
from app.services.retrieval_index import delete_item_chunks

logger = logging.getLogger(__name__)


def _remove_vectors(item_ids: list[str]) -> None:
    for item_id in item_ids:
        try:
            delete_item_chunks(item_id)
        except Exception:
            # SQL activation/scope already excludes deleted sources even if the
            # rebuildable remote projection is temporarily unreachable.
            logger.warning("Qdrant cleanup pending for deleted source %s", item_id)


def delete_source(session: Session, item: ShreckWorldLibraryItem) -> None:
    item_id = item.id
    # Authored sources have no physical original document; never resolve an empty
    # path to '.' and accidentally remove the service's working directory.
    stored = item.storage_path or item.pdf_path
    media = get_settings().media_root.resolve()
    root = Path(stored).resolve().parent if stored and stored != 'pending' else None
    if root is not None and (root == media or media not in root.parents):
        root = None
    projected = session.scalar(select(ShreckWorldDocument.id).where(ShreckWorldDocument.library_item_id == item_id).limit(1))
    session.delete(item)
    session.commit()
    if root is not None:
        shutil.rmtree(root, ignore_errors=True)
    if projected:
        _remove_vectors([item_id])


class OntologyOwnedSourcesError(ValueError):
    """A compatibility collection must not cascade-delete ontology-owned items."""


def delete_world(session: Session, world: ShreckWorld) -> None:
    if session.scalar(select(ShreckWorldLibraryItem.id).where(ShreckWorldLibraryItem.shreckworld_id == world.id, ShreckWorldLibraryItem.ontology_id.is_not(None)).limit(1)):
        raise OntologyOwnedSourcesError("This legacy world contains ontology-owned items and cannot be deleted")
    projected = list(session.scalars(select(ShreckWorldDocument.library_item_id).join(ShreckWorldLibraryItem)
        .where(ShreckWorldLibraryItem.shreckworld_id == world.id).distinct()))
    root = get_settings().media_root / "shreckworlds" / world.id
    session.delete(world)
    session.commit()
    shutil.rmtree(root, ignore_errors=True)
    _remove_vectors(projected)
