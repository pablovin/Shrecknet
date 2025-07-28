from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from app.database import get_session
from app.crud import crud_world_embedding, crud_agent_embedding
from uuid import uuid4
from pathlib import Path
import json
from app.config import settings
from app.task_queue import task_rebuild_world_embedding
from app.models.model_user import User, UserRole
from app.schemas.schema_world_embedding import WorldEmbedding, WorldEmbeddingCreate
from app.models.model_world_embedding import WorldEmbedding as WorldEmbeddingModel
from app.dependencies import get_current_user, require_role

router = APIRouter(
    prefix="/world_embeddings",
    tags=["WorldEmbeddings"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/", response_model=WorldEmbedding)
async def create_embedding(
    embedding: WorldEmbeddingCreate,
    user: User = Depends(require_role(UserRole.system_admin)),
    session: AsyncSession = Depends(get_session),
):
    db_embedding = WorldEmbeddingModel(**embedding.model_dump())
    created = await crud_world_embedding.create_embedding(session, db_embedding)

    job_id = uuid4().hex
    job_dir = Path(settings.world_embedding_job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    job_path = job_dir / f"{job_id}.json"
    with open(job_path, "w") as f:
        json.dump({"status": "queued", "embedding_id": created.id, "job_type": "rebuild_world_embedding"}, f)
    task_rebuild_world_embedding.delay(created.id, job_id)
    return created


@router.get("/", response_model=list[WorldEmbedding])
async def list_embeddings(
    world_id: int | None = None, session: AsyncSession = Depends(get_session)
):
    return await crud_world_embedding.get_embeddings(session, world_id)


@router.delete("/{embedding_id}")
async def delete_embedding(
    embedding_id: int,
    user: User = Depends(require_role(UserRole.system_admin)),
    session: AsyncSession = Depends(get_session),
):
    ok = await crud_world_embedding.delete_embedding(session, embedding_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Embedding not found")
    return {"ok": True}


@router.post("/{embedding_id}/embed_async")
async def embed_world_async(
    embedding_id: int,
    user: User = Depends(require_role(UserRole.system_admin)),
):
    job_id = uuid4().hex
    job_dir = Path(settings.world_embedding_job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    job_path = job_dir / f"{job_id}.json"
    with open(job_path, "w") as f:
        json.dump({"status": "queued", "embedding_id": embedding_id, "job_type": "rebuild_world_embedding"}, f)
    task_rebuild_world_embedding.delay(embedding_id, job_id)
    return {"job_id": job_id}


@router.get("/agents/{agent_id}")
async def get_agent_embeddings(
    agent_id: int, session: AsyncSession = Depends(get_session)
):
    return await crud_agent_embedding.get_embeddings(session, agent_id)


class AgentEmbeddingUpdate(BaseModel):
    embedding_ids: list[int]


@router.post("/agents/{agent_id}")
async def set_agent_embeddings(
    agent_id: int,
    payload: AgentEmbeddingUpdate,
    user: User = Depends(require_role(UserRole.world_builder)),
    session: AsyncSession = Depends(get_session),
):
    await crud_agent_embedding.set_embeddings(session, agent_id, payload.embedding_ids)
    return {"ok": True}


@router.get("/vector_jobs/{job_id}")
async def world_embedding_job_status(job_id: str):
    job_path = Path(settings.world_embedding_job_dir) / f"{job_id}.json"
    if not job_path.is_file():
        raise HTTPException(status_code=404, detail="Job not found")
    with open(job_path) as f:
        data = json.load(f)
    return data


@router.get("/vector_jobs")
async def list_world_embedding_jobs():
    job_dir = Path(settings.world_embedding_job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for p in job_dir.glob("*.json"):
        with open(p) as f:
            data = json.load(f)
        data["job_id"] = p.stem
        jobs.append(data)
    return jobs
