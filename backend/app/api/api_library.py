from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path

from app.database import get_session
from app.models.model_user import User, UserRole
from app.models.model_library_item import LibraryItem
from app.schemas.schema_library_item import LibraryItemRead, LibraryItemUpdate
from app.crud.crud_library_item import (
    create_item,
    get_items,
    get_item,
    update_item,
    delete_item,
)
from app.dependencies import get_current_user, require_role

LibraryItemRead.model_rebuild()

router = APIRouter(prefix="/library", tags=["Library"], dependencies=[Depends(get_current_user)])

@router.post("/", response_model=LibraryItemRead)
async def create_library_item(
    name: str = Form(...),
    system: str = Form(...),
    description: str = Form(None),
    file: UploadFile = File(...),
    user: User = Depends(require_role(UserRole.system_admin)),
    session: AsyncSession = Depends(get_session),
):
    dest_dir = Path("data") / "library" / "system" / system
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename).suffix
    safe_name = Path(name).stem or "item"
    dest_path = dest_dir / f"{safe_name}{ext}"
    with open(dest_path, "wb") as out:
        out.write(await file.read())
    item = LibraryItem(
        name=name,
        system=system,
        description=description,
        path=str(dest_path),
    )
    return await create_item(session, item)

@router.get("/", response_model=list[LibraryItemRead])
async def list_library_items(
    system: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    return await get_items(session, system)

@router.get("/{item_id}", response_model=LibraryItemRead)
async def read_library_item(item_id: int, session: AsyncSession = Depends(get_session)):
    item = await get_item(session, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item

@router.patch("/{item_id}", response_model=LibraryItemRead)
async def update_library_item_endpoint(
    item_id: int,
    updates: LibraryItemUpdate,
    user: User = Depends(require_role(UserRole.system_admin)),
    session: AsyncSession = Depends(get_session),
):
    update_dict = updates.model_dump(exclude_unset=True)
    item = await update_item(session, item_id, update_dict)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item

@router.delete("/{item_id}")
async def delete_library_item_endpoint(
    item_id: int,
    user: User = Depends(require_role(UserRole.system_admin)),
    session: AsyncSession = Depends(get_session),
):
    ok = await delete_item(session, item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"ok": True}
