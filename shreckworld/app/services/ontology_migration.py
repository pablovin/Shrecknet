"""Explicit, repeatable migration of legacy sources to direct ontology ownership."""
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from app.integrations.ontologies import resolve_ontology, shrecknet_read
from app.models import ShreckWorld, ShreckWorldLibraryItem
from app.schemas.ontology_sources import OntologyMigrationEntry, WorldOntologyLink
from pydantic import TypeAdapter, ValidationError


def migration_plan(session: Session, token: str, mappings: dict[str, int]) -> list[OntologyMigrationEntry]:
    try:
        worlds = TypeAdapter(list[WorldOntologyLink]).validate_python(shrecknet_read('/worlds', token))
    except ValidationError as exc:
        raise ValueError('Invalid Shrecknet world directory') from exc
    groups = {world.id: world.ontology_ids for world in worlds}
    collections = list(session.scalars(select(ShreckWorld).order_by(ShreckWorld.id)))
    if set(mappings) - {collection.id for collection in collections}:
        raise ValueError('Mapping contains an unknown legacy collection')
    rows = []
    for collection in collections:
        items = list(session.scalars(select(ShreckWorldLibraryItem).where(ShreckWorldLibraryItem.shreckworld_id == collection.id)))
        candidates = groups.get(str(collection.shrecknet_world_id), [])
        target = mappings.get(collection.id)
        if target is None and len(candidates) == 1:
            target = candidates[0]
        if target is not None:
            if type(target) is not int or target <= 0:
                raise ValueError('Ontology IDs must be positive integers')
            resolve_ontology(target, token)
            if any(item.ontology_id not in {None, target} for item in items):
                raise ValueError('Migration cannot reassign an already migrated item')
        rows.append(OntologyMigrationEntry(collection_id=collection.id, name=collection.name, ontology_id=target, item_count=len(items),
                     pending_items=sum(item.ontology_id is None for item in items), status='ready' if target is not None else 'needs_mapping'))
    return rows


def apply_migration(session: Session, plan: list[OntologyMigrationEntry]) -> int:
    changed = 0
    try:
        for row in plan:
            if row.ontology_id is None:
                continue
            result = session.execute(update(ShreckWorldLibraryItem).where(
                ShreckWorldLibraryItem.shreckworld_id == row.collection_id,
                ShreckWorldLibraryItem.ontology_id.is_(None)).values(ontology_id=row.ontology_id))
            changed += result.rowcount
            conflict = session.scalar(select(ShreckWorldLibraryItem.id).where(
                ShreckWorldLibraryItem.shreckworld_id == row.collection_id,
                ShreckWorldLibraryItem.ontology_id != row.ontology_id).limit(1))
            if conflict:
                raise ValueError('Migration target changed; preview again')
        session.commit()
    except Exception:
        session.rollback()
        raise
    return changed
