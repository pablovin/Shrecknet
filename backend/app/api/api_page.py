from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from fastapi import Query
from app.database import get_session
from app.models.model_user import User, UserRole
from app.models.model_page import (
    Page,
    PageCharacteristicValue,
    PageChange,
    PageKeyEvent,
    PageRelationship,
)
from app.schemas.schema_page import PageCreate, PageRead, PageUpdate, PageSummary
from app.schemas.schema_page_characteristic_value import PageCharacteristicValueUpdate, PageCharacteristicValueRead, PageCharacteristicValueCreate
from app.schemas.schema_page_change import PageChangeRead
from app.schemas.schema_page_key_event import (
    PageKeyEventCreate,
    PageKeyEventRead,
    PageKeyEventUpdate,
)
from app.schemas.schema_page_relationship import (
    PageRelationshipCreate,
    PageRelationshipRead,
    PageRelationshipUpdate,
)
from app.crud.crud_page import (
    create_page,
    get_pages,
    search_pages,
    get_page,
    update_page,
    delete_page,
    create_page_characteristic_value,
    get_page_characteristic_values,
    get_pages_characteristic_values,
    get_pages_key_events,
    get_pages_relationships,
    get_pages_changes,
    update_page_characteristic_value,
    delete_page_characteristic_value,
    delete_page_characteristic_values,
    create_page_change,
    get_page_changes,
    create_key_event,
    get_key_events,
    update_key_event,
    delete_key_event,
    create_relationship,
    get_relationships,
    update_relationship,
    delete_relationship,
)
from datetime import datetime, timezone
from app.dependencies import get_current_user, require_role



PageCharacteristicValueUpdate.model_rebuild()
PageCharacteristicValueRead.model_rebuild()    
PageCharacteristicValueCreate.model_rebuild() 


PageCreate.model_rebuild()
PageRead.model_rebuild()    
PageUpdate.model_rebuild() 



router = APIRouter(prefix="/pages", tags=["Pages"], dependencies=[Depends(get_current_user)])
# -- Page CRUD

# PageCreate.model_rebuild()
# PageRead.model_rebuild()


@router.post("/", response_model=PageRead)
async def create_page_endpoint(
    page: PageCreate,
    user: User = Depends(require_role(UserRole.writer)),
    session: AsyncSession = Depends(get_session),
):

    page_data = page.model_dump(exclude={"values"})
    db_page = Page(
        **page_data,
        created_by_user_id=user.id,
        created_at=datetime.now(timezone.utc),
    )
    db_page = await create_page(session, db_page)
    change = PageChange(
        page_id=db_page.id,
        change_type="created",
        author_type="user",
        author_id=user.id,
    )
    await create_page_change(session, change)

    # values = await get_page_characteristic_values(session, db_page.id)


    # --- Store characteristic values ---
    # print (f"All page to be inserted: {page}")
    # print (f"All values to be inserted: {page.values}")
    seen_characteristic_ids = set()
    for val in page.values or []:
        if val.characteristic_id in seen_characteristic_ids:
            continue
        seen_characteristic_ids.add(val.characteristic_id)
        value_obj = PageCharacteristicValue(
            page_id=db_page.id,
            characteristic_id=val.characteristic_id,
            value=val.value or []
        )
        await create_page_characteristic_value(session, value_obj)
    # --- Return the page, with values loaded! ---
    # fetch values to return as part of PageRead


    values = await get_page_characteristic_values(session, db_page.id)
    events = await get_key_events(session, db_page.id)
    relationships = await get_relationships(session, db_page.id)
    changes = await get_page_changes(session, db_page.id)
    response = PageRead.model_validate({
        **db_page.model_dump(),
        "values": values,
        "key_events": events,
        "relationship_map": relationships,
        "changelog": changes,
    })

     # Schedule background crosslink update only if this page allows
    if not db_page.ignore_crosslink:
        from app.task_queue import task_auto_crosslink_batch, task_sync_page_ref_attributes
        task_auto_crosslink_batch.delay(db_page.id)
        task_sync_page_ref_attributes.delay(db_page.id)
    return response


@router.get("/search", response_model=List[PageSummary])
async def search_pages_endpoint(
    q: Optional[str] = Query(None, alias="query"),
    gameworld_id: Optional[int] = Query(None),
    concept_id: Optional[int] = Query(None),
    session: AsyncSession = Depends(get_session),
):
    pages = await search_pages(
        session,
        search=q,
        gameworld_id=gameworld_id,
        concept_id=concept_id,
    )
    return pages

    # values = await get_page_characteristic_values(session, db_page.id)
    # return PageRead.model_validate({**db_page.model_dump(), "values": values})
    # return db_page




@router.get("/", response_model=List[PageRead])
async def read_pages(
    gameworld_id: Optional[int] = Query(None),
    concept_id: Optional[int] = Query(None),
    session: AsyncSession = Depends(get_session),
):
    db_pages = await get_pages(session, gameworld_id=gameworld_id, concept_id=concept_id)
    page_id_list = [p.id for p in db_pages]
    values_map = await get_pages_characteristic_values(session, page_id_list)
    events_map = await get_pages_key_events(session, page_id_list)
    relationships_map = await get_pages_relationships(session, page_id_list)
    changes_map = await get_pages_changes(session, page_id_list)
    return [
        PageRead.model_validate(
            {
                **page.model_dump(),
                "values": values_map.get(page.id, []),
                "key_events": events_map.get(page.id, []),
                "relationship_map": relationships_map.get(page.id, []),
                "changelog": changes_map.get(page.id, []),
            }
        )
        for page in db_pages
    ]

@router.get("/{page_id}", response_model=PageRead)
async def read_page(
    page_id: int,
    session: AsyncSession = Depends(get_session),
):
    db_page = await get_page(session, page_id)
    if not db_page:
        raise HTTPException(status_code=404, detail="Page not found")
    
    values = await get_page_characteristic_values(session, db_page.id)
    events = await get_key_events(session, db_page.id)
    relationships = await get_relationships(session, db_page.id)
    changes = await get_page_changes(session, db_page.id)
    return PageRead.model_validate(
        {
            **db_page.model_dump(),
            "values": values,
            "key_events": events,
            "relationship_map": relationships,
            "changelog": changes,
        }
    )
        

@router.patch("/{page_id}", response_model=PageRead)
async def update_page_endpoint(
    page_id: int,
    updates: PageUpdate,
    user: User = Depends(require_role(UserRole.writer)),
    session: AsyncSession = Depends(get_session),
):
    db_page = await get_page(session, page_id)
    if not db_page:
        raise HTTPException(status_code=404, detail="Page not found")

    if not (
        require_role(UserRole.writer)
        or user.id in db_page.allowed_user_ids
    ):
        raise HTTPException(status_code=403, detail="You are not allowed to update this page.")

    update_dict = updates.model_dump(exclude_unset=True, exclude={"values"})
    db_page = await update_page(session, page_id, update_dict)
    change = PageChange(
        page_id=page_id,
        change_type="updated",
        author_type="user",
        author_id=user.id,
        values=update_dict,
    )
    await create_page_change(session, change)



    # --- Update characteristic values, if provided ---
    if updates.values is not None:
        # Remove all existing values for this page
        await delete_page_characteristic_values(session, page_id)        
        # Add the new ones without duplicates
        seen_characteristic_ids = set()
        for val in updates.values:
            if val.characteristic_id in seen_characteristic_ids:
                continue
            seen_characteristic_ids.add(val.characteristic_id)
            print (f"VAL: {val} - {val.model_dump()}")
            value_obj = PageCharacteristicValue(
                page_id=page_id,
                characteristic_id=val.characteristic_id,
                value=val.value
            )
            await create_page_characteristic_value(session, value_obj)

    # Fetch updated values to return
    values = await get_page_characteristic_values(session, page_id)
    events = await get_key_events(session, page_id)
    relationships = await get_relationships(session, page_id)
    changes = await get_page_changes(session, page_id)
    response = PageRead.model_validate({
        **db_page.model_dump(),
        "values": values,
        "key_events": events,
        "relationship_map": relationships,
        "changelog": changes,
    })

    print ("Page was updated with the right values, now going into auto_crosslink!")
    from app.task_queue import task_auto_crosslink_page_content, task_sync_page_ref_attributes
    task_auto_crosslink_page_content.delay(db_page.id)
    task_sync_page_ref_attributes.delay(db_page.id)

    return response

@router.delete("/{page_id}")
async def delete_page_endpoint(
    page_id: int,
    user: User = Depends(require_role(UserRole.writer)),
    session: AsyncSession = Depends(get_session),
):
    db_page = await get_page(session, page_id)
    if not db_page:
        raise HTTPException(status_code=404, detail="Page not found")
    # Only writers and up
    if user.role not in [UserRole.writer, UserRole.world_builder, UserRole.system_admin]:
        raise HTTPException(status_code=403, detail="You are not allowed to delete this page.")
    await delete_page(session, page_id)

    from app.task_queue import (
        task_remove_page_refs_from_characteristics,
        task_remove_crosslinks_to_page,
    )
    task_remove_page_refs_from_characteristics.delay(db_page.id)
    task_remove_crosslinks_to_page.delay(page_id)
    
    return {"ok": True}

# -- PageCharacteristicValue endpoints (optional, add as needed)

@router.post("/value/")
async def add_page_value(
    value: dict,  # Should match your PageCharacteristicValueCreate schema
    user: User = Depends(require_role(UserRole.writer)),
    session: AsyncSession = Depends(get_session),
):
    val = await create_page_characteristic_value(session, value)
    return {"ok": True, "value": val}

@router.patch("/value/")
async def update_page_value(
    page_id: int,
    characteristic_id: int,
    new_value: str,
    user: User = Depends(require_role(UserRole.writer)),
    session: AsyncSession = Depends(get_session),
):
    val = await update_page_characteristic_value(session, page_id, characteristic_id, new_value)
    return {"ok": True, "value": val}

@router.delete("/value/")
async def delete_page_value(
    page_id: int,
    characteristic_id: int,
    user: User = Depends(require_role(UserRole.writer)),
    session: AsyncSession = Depends(get_session),
):
    await delete_page_characteristic_value(session, page_id, characteristic_id)
    return {"ok": True}

# -- Key Event endpoints --

@router.post("/{page_id}/events/", response_model=PageKeyEventRead)
async def add_key_event_endpoint(
    page_id: int,
    event: PageKeyEventCreate,
    user: User = Depends(require_role(UserRole.writer)),
    session: AsyncSession = Depends(get_session),
):
    if event.page_id != page_id:
        event.page_id = page_id
    db_event = await create_key_event(session, PageKeyEvent(**event.model_dump()))
    change = PageChange(
        page_id=page_id,
        change_type="key_event_added",
        author_type="user",
        author_id=user.id,
        values=db_event.model_dump(),
    )
    await create_page_change(session, change)
    return db_event


@router.patch("/events/{event_id}", response_model=PageKeyEventRead)
async def update_key_event_endpoint(
    event_id: int,
    updates: PageKeyEventUpdate,
    user: User = Depends(require_role(UserRole.writer)),
    session: AsyncSession = Depends(get_session),
):
    update_dict = updates.model_dump(exclude_unset=True)
    event = await update_key_event(session, event_id, update_dict)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    change = PageChange(
        page_id=event.page_id,
        change_type="key_event_updated",
        author_type="user",
        author_id=user.id,
        values=update_dict,
    )
    await create_page_change(session, change)
    return event


@router.delete("/events/{event_id}")
async def delete_key_event_endpoint(
    event_id: int,
    user: User = Depends(require_role(UserRole.writer)),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(PageKeyEvent).where(PageKeyEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    page_id = event.page_id
    await delete_key_event(session, event_id)
    change = PageChange(
        page_id=page_id,
        change_type="key_event_deleted",
        author_type="user",
        author_id=user.id,
        values={"event_id": event_id},
    )
    await create_page_change(session, change)
    return {"ok": True}

# -- Relationship endpoints --

@router.post("/{page_id}/relationships/", response_model=PageRelationshipRead)
async def add_relationship_endpoint(
    page_id: int,
    rel: PageRelationshipCreate,
    user: User = Depends(require_role(UserRole.writer)),
    session: AsyncSession = Depends(get_session),
):
    if rel.page_id != page_id:
        rel.page_id = page_id
    db_rel = await create_relationship(session, PageRelationship(**rel.model_dump()))
    change = PageChange(
        page_id=page_id,
        change_type="relationship_added",
        author_type="user",
        author_id=user.id,
        values=db_rel.model_dump(),
    )
    await create_page_change(session, change)
    return db_rel


@router.patch("/relationships/{rel_id}", response_model=PageRelationshipRead)
async def update_relationship_endpoint(
    rel_id: int,
    updates: PageRelationshipUpdate,
    user: User = Depends(require_role(UserRole.writer)),
    session: AsyncSession = Depends(get_session),
):
    update_dict = updates.model_dump(exclude_unset=True)
    rel = await update_relationship(session, rel_id, update_dict)
    if not rel:
        raise HTTPException(status_code=404, detail="Relationship not found")
    change = PageChange(
        page_id=rel.page_id,
        change_type="relationship_updated",
        author_type="user",
        author_id=user.id,
        values=update_dict,
    )
    await create_page_change(session, change)
    return rel


@router.delete("/relationships/{rel_id}")
async def delete_relationship_endpoint(
    rel_id: int,
    user: User = Depends(require_role(UserRole.writer)),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(PageRelationship).where(PageRelationship.id == rel_id))
    rel = result.scalar_one_or_none()
    if not rel:
        raise HTTPException(status_code=404, detail="Relationship not found")
    page_id = rel.page_id
    await delete_relationship(session, rel_id)
    change = PageChange(
        page_id=page_id,
        change_type="relationship_deleted",
        author_type="user",
        author_id=user.id,
        values={"relationship_id": rel_id},
    )
    await create_page_change(session, change)
    return {"ok": True}
