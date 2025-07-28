from typing import List, Optional, Dict
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import delete

from app.models.model_page import (
    Page,
    PageCharacteristicValue,
    PageKeyEvent,
    PageRelationship,
    PageChange,
)
from app.models.model_characteristic import Characteristic
from app.schemas.schema_page import PageCreate, PageUpdate, PageSummary
from app.schemas.schema_page_characteristic_value import PageCharacteristicValueCreate

# --- PAGE CRUD ---

async def create_page(session: AsyncSession, page: Page) -> Page:
    session.add(page)
    await session.commit()
    await session.flush()
    return page

async def get_page(session: AsyncSession, page_id: int) -> Optional[Page]:
    result = await session.execute(
        select(Page).where(Page.id == page_id)
    )
    return result.scalar_one_or_none()

async def get_pages(session: AsyncSession, *, gameworld_id: Optional[int] = None, concept_id: Optional[int] = None) -> List[Page]:
    query = select(Page)
    if gameworld_id:
        query = query.where(Page.gameworld_id == gameworld_id)
    if concept_id:
        query = query.where(Page.concept_id == concept_id)
    result = await session.execute(query)
    return result.scalars().all()

async def search_pages(
    session: AsyncSession,
    *,
    search: Optional[str] = None,
    gameworld_id: Optional[int] = None,
    concept_id: Optional[int] = None
) -> List[PageSummary]:
    query = select(Page.id, Page.name, Page.gameworld_id, Page.concept_id, Page.logo)
    if gameworld_id:
        query = query.where(Page.gameworld_id == gameworld_id)
    if concept_id:
        query = query.where(Page.concept_id == concept_id)
    if search:
        like = f"%{search}%"
        query = query.where(Page.name.ilike(like))
    query = query.order_by(Page.name)
    result = await session.execute(query)
    rows = result.all()
    return [
        PageSummary(
            id=row.id,
            name=row.name,
            gameworld_id=row.gameworld_id,
            concept_id=row.concept_id,
            logo=row.logo,
        )
        for row in rows
    ]

async def update_page(session: AsyncSession, page_id: int, updates: dict) -> Optional[Page]:
    db_page = await get_page(session, page_id)
    if not db_page:
        return None
    for k, v in updates.items():
        setattr(db_page, k, v)
    db_page.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.flush()
    return db_page

async def delete_page(session: AsyncSession, page_id: int) -> bool:
    page = await get_page(session, page_id)
    if not page:
        return False

    # Remove references to this page in page_ref characteristics within the same world
    result = await session.execute(
        select(Characteristic).where(
            (Characteristic.type == "page_ref") &
            (Characteristic.gameworld_id == page.gameworld_id)
        )
    )
    page_ref_characteristics = result.scalars().all()
    for char in page_ref_characteristics:
        res_vals = await session.execute(
            select(PageCharacteristicValue).where(
                PageCharacteristicValue.characteristic_id == char.id
            )
        )
        vals = res_vals.scalars().all()
        for pcv in vals:
            if not pcv.value:
                continue
            value_list = [str(v) for v in pcv.value]
            if str(page_id) in value_list:
                pcv.value = [v for v in value_list if v != str(page_id)]

    await session.delete(page)
    await session.commit()
    await session.flush()
    return True

# --- PAGE CHARACTERISTIC VALUE CRUD ---

async def create_page_characteristic_value(session: AsyncSession, value_obj: PageCharacteristicValue) -> PageCharacteristicValue:
    existing = await session.get(
        PageCharacteristicValue,
        (value_obj.page_id, value_obj.characteristic_id),
    )
    if existing:
        existing.value = value_obj.value
        await session.commit()
        await session.flush()
        return existing
    session.add(value_obj)
    await session.commit()
    await session.flush()
    return value_obj

async def get_page_characteristic_values(session: AsyncSession, page_id: int) -> List[PageCharacteristicValue]:
    result = await session.execute(
        select(PageCharacteristicValue).where(PageCharacteristicValue.page_id == page_id)
    )
    return result.scalars().all()

async def get_pages_characteristic_values(
    session: AsyncSession, page_ids: List[int]
) -> Dict[int, List[PageCharacteristicValue]]:
    if not page_ids:
        return {}
    result = await session.execute(
        select(PageCharacteristicValue).where(PageCharacteristicValue.page_id.in_(page_ids))
    )
    all_values = result.scalars().all()
    values_by_page: Dict[int, List[PageCharacteristicValue]] = {}
    for val in all_values:
        values_by_page.setdefault(val.page_id, []).append(val)
    return values_by_page

async def delete_page_characteristic_values(session: AsyncSession, page_id: int) -> None:
    await session.execute(
        delete(PageCharacteristicValue).where(PageCharacteristicValue.page_id == page_id)
    )
    await session.commit()
    await session.flush()

# --- Page key events, relationships and changelog CRUD ---

async def create_key_event(session: AsyncSession, event: PageKeyEvent) -> PageKeyEvent:
    session.add(event)
    await session.commit()
    await session.flush()
    return event

async def get_key_events(session: AsyncSession, page_id: int) -> List[PageKeyEvent]:
    result = await session.execute(
        select(PageKeyEvent).where(PageKeyEvent.page_id == page_id).order_by(PageKeyEvent.added_at)
    )
    return result.scalars().all()

async def get_pages_key_events(
    session: AsyncSession, page_ids: List[int]
) -> Dict[int, List[PageKeyEvent]]:
    if not page_ids:
        return {}
    result = await session.execute(
        select(PageKeyEvent)
        .where(PageKeyEvent.page_id.in_(page_ids))
        .order_by(PageKeyEvent.added_at)
    )
    events = result.scalars().all()
    events_by_page: Dict[int, List[PageKeyEvent]] = {}
    for ev in events:
        events_by_page.setdefault(ev.page_id, []).append(ev)
    return events_by_page

async def update_key_event(session: AsyncSession, event_id: int, updates: dict) -> Optional[PageKeyEvent]:
    result = await session.execute(select(PageKeyEvent).where(PageKeyEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        return None
    for key, value in updates.items():
        setattr(event, key, value)
    await session.commit()
    await session.flush()
    return event

async def delete_key_event(session: AsyncSession, event_id: int) -> None:
    await session.execute(delete(PageKeyEvent).where(PageKeyEvent.id == event_id))
    await session.commit()
    await session.flush()

async def create_relationship(session: AsyncSession, rel: PageRelationship) -> PageRelationship:
    session.add(rel)
    inverse_dir = "incoming" if rel.direction == "outgoing" else "outgoing"
    result = await session.execute(
        select(PageRelationship).where(
            PageRelationship.page_id == rel.target_page_id,
            PageRelationship.target_page_id == rel.page_id,
            PageRelationship.relationship_type == rel.relationship_type,
            PageRelationship.direction == inverse_dir,
            PageRelationship.source_page_id == rel.source_page_id,
        )
    )
    existing = result.scalar_one_or_none()
    if not existing:
        inverse = PageRelationship(
            page_id=rel.target_page_id,
            target_page_id=rel.page_id,
            relationship_type=rel.relationship_type,
            direction=inverse_dir,
            source_page_id=rel.source_page_id,
            description=rel.description,
            author_type=rel.author_type,
            author_id=rel.author_id,
        )
        session.add(inverse)
    await session.commit()
    await session.flush()
    return rel

async def get_relationships(session: AsyncSession, page_id: int) -> List[PageRelationship]:
    result = await session.execute(
        select(PageRelationship).where(PageRelationship.page_id == page_id).order_by(PageRelationship.added_at)
    )
    return result.scalars().all()

async def get_pages_relationships(
    session: AsyncSession, page_ids: List[int]
) -> Dict[int, List[PageRelationship]]:
    if not page_ids:
        return {}
    result = await session.execute(
        select(PageRelationship)
        .where(PageRelationship.page_id.in_(page_ids))
        .order_by(PageRelationship.added_at)
    )
    rels = result.scalars().all()
    rels_by_page: Dict[int, List[PageRelationship]] = {}
    for rel in rels:
        rels_by_page.setdefault(rel.page_id, []).append(rel)
    return rels_by_page

async def update_relationship(session: AsyncSession, rel_id: int, updates: dict) -> Optional[PageRelationship]:
    result = await session.execute(select(PageRelationship).where(PageRelationship.id == rel_id))
    rel = result.scalar_one_or_none()
    if not rel:
        return None
    for key, value in updates.items():
        setattr(rel, key, value)
    await session.commit()
    await session.flush()
    return rel

async def delete_relationship(session: AsyncSession, rel_id: int) -> None:
    await session.execute(delete(PageRelationship).where(PageRelationship.id == rel_id))
    await session.commit()
    await session.flush()

from app.utils import serialize_value

async def create_page_change(session: AsyncSession, change: PageChange) -> PageChange:
    if change.values is not None:
        change.values = serialize_value(change.values)
    session.add(change)
    await session.commit()
    await session.flush()
    return change

async def get_page_changes(session: AsyncSession, page_id: int) -> List[PageChange]:
    result = await session.execute(
        select(PageChange).where(PageChange.page_id == page_id).order_by(PageChange.date)
    )
    return result.scalars().all()

async def get_pages_changes(
    session: AsyncSession, page_ids: List[int]
) -> Dict[int, List[PageChange]]:
    if not page_ids:
        return {}
    result = await session.execute(
        select(PageChange)
        .where(PageChange.page_id.in_(page_ids))
        .order_by(PageChange.date)
    )
    changes = result.scalars().all()
    changes_by_page: Dict[int, List[PageChange]] = {}
    for change in changes:
        changes_by_page.setdefault(change.page_id, []).append(change)
    return changes_by_page

# --- Optional: update individual value (not used in atomic pattern) ---

async def update_page_characteristic_value(session: AsyncSession, page_id: int, characteristic_id: int, value: List[str]):
    result = await session.execute(
        select(PageCharacteristicValue).where(
            PageCharacteristicValue.page_id == page_id,
            PageCharacteristicValue.characteristic_id == characteristic_id
        )
    )
    val = result.scalar_one_or_none()
    if val:
        val.value = value
        await session.commit()
        await session.flush()
    return val

async def delete_page_characteristic_value(session: AsyncSession, page_id: int, characteristic_id: int) -> None:
    await session.execute(
        delete(PageCharacteristicValue).where(
            PageCharacteristicValue.page_id == page_id,
            PageCharacteristicValue.characteristic_id == characteristic_id
        )
    )
    await session.commit()
    await session.flush()