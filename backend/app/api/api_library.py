from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path
from uuid import uuid4
import json
import os

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
from app.config import settings

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


@router.get("/vector_jobs/{job_id}")
async def library_vector_job_status(job_id: str):
    job_path = Path(settings.library_job_dir) / f"{job_id}.json"
    if not job_path.is_file():
        raise HTTPException(status_code=404, detail="Job not found")
    with open(job_path) as f:
        data = json.load(f)
    return data


@router.get("/vector_jobs")
async def list_library_vector_jobs():
    job_dir = Path(settings.library_job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for p in job_dir.glob("*.json"):
        with open(p) as f:
            data = json.load(f)
        data["job_id"] = p.stem
        jobs.append(data)
    return jobs

@router.get("/{item_id}", response_model=LibraryItemRead)
async def read_library_item(item_id: int, session: AsyncSession = Depends(get_session)):
    item = await get_item(session, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.get("/{item_id}/download")
async def download_library_item(
    item_id: int,
    session: AsyncSession = Depends(get_session),
):
    item = await get_item(session, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not item.path or not os.path.isfile(item.path):
        raise HTTPException(status_code=404, detail="File not found")
    filename = Path(item.path).name
    return FileResponse(path=item.path, filename=filename)

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


@router.post("/{item_id}/embed_async")
async def embed_library_item_async(
    item_id: int,
    user: User = Depends(require_role(UserRole.system_admin)),
):
    from app.task_queue import task_rebuild_library_vectors
    job_id = uuid4().hex
    job_dir = Path(settings.library_job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    job_path = job_dir / f"{job_id}.json"
    with open(job_path, "w") as f:
        json.dump({"status": "queued", "item_id": item_id, "job_type": "rebuild_library_vectors"}, f)
    task_rebuild_library_vectors.delay(item_id, job_id)
    return {"job_id": job_id}
