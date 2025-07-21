from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import uuid4
import json
from pathlib import Path
import base64

from app.dependencies import get_current_user
from app.models.model_user import User
from app.database import get_session
from app.crud import (
    crud_specialist_source,
    crud_specialist_vectordb,
    crud_chat_history,
    crud_agent_library_item,
    crud_library_item,
)
from app.crud.crud_specialist_agent import chat_with_specialist
from app.models.model_library_item import LibraryItem
from app.schemas.schema_library_item import LibraryItemRead
from app.models.model_specialist_source import SpecialistSource
from app.schemas.schema_specialist_source import SpecialistSourceCreate, SpecialistSourceRead
from pydantic import BaseModel
from typing import Literal
from app.config import settings
from fastapi import Response

router = APIRouter(prefix="/specialist_agents", tags=["SpecialistAgents"], dependencies=[Depends(get_current_user)])

@router.get("/{agent_id}/items", response_model=list[LibraryItemRead])
async def list_items(agent_id: int, session: AsyncSession = Depends(get_session)):
    return await crud_agent_library_item.get_items(session, agent_id)

class ItemLink(BaseModel):
    item_id: int

@router.post("/{agent_id}/items", response_model=LibraryItemRead)
async def add_item(agent_id: int, payload: ItemLink, session: AsyncSession = Depends(get_session)):
    await crud_agent_library_item.add_item(session, agent_id, payload.item_id)
    item = await crud_library_item.get_item(session, payload.item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item

@router.delete("/{agent_id}/items/{item_id}")
async def remove_item(agent_id: int, item_id: int, session: AsyncSession = Depends(get_session)):
    ok = await crud_agent_library_item.delete_item(session, agent_id, item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"ok": True}


@router.post("/{agent_id}/sources", response_model=SpecialistSourceRead)
async def add_source(agent_id: int, payload: SpecialistSourceCreate, session: AsyncSession = Depends(get_session)):
    source = SpecialistSource(agent_id=agent_id, **payload.model_dump())
    return await crud_specialist_source.add_source(session, source)

@router.post("/{agent_id}/source_file", response_model=SpecialistSourceRead)
async def upload_source_file(
    agent_id: int,
    file: UploadFile = File(...),
    name: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
        
    ext = Path(file.filename).suffix.lower()
    if ext not in [".pdf", ".txt"]:
        raise HTTPException(status_code=400, detail="Only .pdf or .txt files allowed")
    dest_dir = Path("data") / "specialist_agents" / str(agent_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(name).stem or "source"
    dest_path = dest_dir / f"{safe_name}{ext}"
    with open(dest_path, "wb") as out:
        out.write(await file.read())

    source = SpecialistSource(
        agent_id=agent_id,
        name=name,
        type="file",
        path=str(dest_path),
    )
    return await crud_specialist_source.add_source(session, source)

@router.get("/{agent_id}/sources", response_model=list[SpecialistSourceRead])
async def list_sources(agent_id: int, session: AsyncSession = Depends(get_session)):
    return await crud_specialist_source.get_sources(session, agent_id)

@router.get("/{agent_id}/sources/{source_id}/download")
async def download_source(agent_id: int, source_id: int, session: AsyncSession = Depends(get_session)):
    source = await crud_specialist_source.get_source(session, source_id)
    if not source or source.agent_id != agent_id or source.type != "file" or not source.path:
        raise HTTPException(status_code=404, detail="Source not found")
    file_path = Path(source.path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    content = file_path.read_bytes()
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{file_path.name}"'}
    )

@router.delete("/{agent_id}/sources/{source_id}")
async def remove_source(agent_id: int, source_id: int, session: AsyncSession = Depends(get_session)):
    ok = await crud_specialist_source.delete_source(session, source_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Source not found")
    return {"ok": True}

@router.post("/{agent_id}/rebuild_vectors")
async def rebuild_vectors(agent_id: int, session: AsyncSession = Depends(get_session)):
    count = await crud_specialist_vectordb.rebuild_agent(session, agent_id)
    return {"documents_indexed": count}


@router.get("/{agent_id}/export_vectordb")
async def export_vectordb(
    agent_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    vectordb = crud_specialist_vectordb.export_agent_vectordb(agent_id)
    sources = await crud_specialist_source.get_sources(session, agent_id)
    exported_sources = []
    for src in sources:
        entry = {
            "id": src.id,
            "name": src.name,
            "type": src.type,
            "url": src.url,
            "path": Path(src.path).name if src.path else None,
            "added_at": src.added_at.isoformat(),
        }
        if src.type == "file" and src.path and Path(src.path).is_file():
            with open(src.path, "rb") as f:
                entry["content"] = base64.b64encode(f.read()).decode("utf-8")
        exported_sources.append(entry)

    data = {"vectordb": vectordb, "sources": exported_sources}
    return Response(
        content=json.dumps(data),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="agent_{agent_id}_export.json"'
        },
    )


@router.post("/{agent_id}/import_vectordb")
async def import_vectordb(
    agent_id: int,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    try:
        content = await file.read()
        data = json.loads(content)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid file")

    sources_data = data.get("sources", [])
    vectordb_data = data.get("vectordb", {})

    # remove existing sources
    await crud_specialist_source.delete_all_sources(session, agent_id)

    id_map: dict[int, int] = {}
    for src in sources_data:
        if src.get("type") == "file":
            filename = src.get("path") or (src.get("name") or "source")
            dest_dir = Path("data") / "specialist_agents" / str(agent_id)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / filename
            if src.get("content"):
                with open(dest_path, "wb") as f:
                    f.write(base64.b64decode(src["content"]))
            new_src = SpecialistSource(
                agent_id=agent_id,
                name=src.get("name") or filename,
                type="file",
                path=str(dest_path),
            )
        else:
            new_src = SpecialistSource(
                agent_id=agent_id,
                name=src.get("name"),
                type="link",
                url=src.get("url"),
            )
        created = await crud_specialist_source.add_source(session, new_src)
        if src.get("id") is not None:
            id_map[src["id"]] = created.id

    # map old source ids to new ones in metadata
    metas = vectordb_data.get("metadatas") or []
    for meta in metas:
        sid = meta.get("source_id")
        if sid in id_map:
            meta["source_id"] = id_map[sid]

    count = await crud_specialist_vectordb.import_agent_vectordb(
        session, agent_id, vectordb_data
    )
    return {"documents_indexed": count}


@router.post("/{agent_id}/rebuild_vectors_async")
async def rebuild_vectors_async(agent_id: int, user: User = Depends(get_current_user)):
    from app.task_queue import task_rebuild_specialist_vectors
    job_id = uuid4().hex
    job_dir = Path(settings.specialist_job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    job_path = job_dir / f"{job_id}.json"
    with open(job_path, "w") as f:
        json.dump({"status": "queued", "agent_id": agent_id, "job_type": "rebuild_specialist_vectors"}, f)
    task_rebuild_specialist_vectors.delay(agent_id, job_id)
    return {"job_id": job_id}


@router.get("/vector_jobs/{job_id}")
async def specialist_vector_job_status(job_id: str):
    job_path = Path(settings.specialist_job_dir) / f"{job_id}.json"
    if not job_path.is_file():
        raise HTTPException(status_code=404, detail="Job not found")
    with open(job_path) as f:
        data = json.load(f)
    return data


@router.get("/vector_jobs")
async def list_vector_jobs():
    job_dir = Path(settings.specialist_job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for p in job_dir.glob("*.json"):
        with open(p) as f:
            data = json.load(f)
        data["job_id"] = p.stem
        jobs.append(data)
    return jobs


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


@router.post("/{agent_id}/chat")
async def specialist_chat(
    agent_id: int,
    payload: ChatRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    msgs = [m.model_dump() for m in payload.messages]
    history = crud_chat_history.load_history(user.id, agent_id)
    user_msg = msgs[-1] if msgs else {"role": "user", "content": ""}
    chat_messages = history + [user_msg]
    assistant_resp = await chat_with_specialist(
        session,
        agent_id,
        chat_messages,
        user_nickname=user.nickname,
    )
    assistant_msg = {"role": "assistant", "content": assistant_resp["answer"]}
    if assistant_resp.get("sources"):
        assistant_msg["sources"] = assistant_resp["sources"]
    new_history = (history + [user_msg, assistant_msg])[-20:]
    crud_chat_history.save_history(user.id, agent_id, new_history)
    return JSONResponse(assistant_resp)


@router.get("/{agent_id}/history")
async def specialist_chat_history(
    agent_id: int,
    limit: int = 20,
    user: User = Depends(get_current_user),
):
    messages = crud_chat_history.load_history(user.id, agent_id)
    return {"messages": messages[-limit:]}


@router.delete("/{agent_id}/history")
async def clear_specialist_history(agent_id: int, user: User = Depends(get_current_user)):
    crud_chat_history.clear_history(user.id, agent_id)
    return {"ok": True}
