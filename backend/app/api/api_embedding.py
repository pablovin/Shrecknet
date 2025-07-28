from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from app.database import get_session
from app.crud import crud_world_embedding, crud_agent_embedding
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
    return await crud_world_embedding.create_embedding(session, db_embedding)


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
