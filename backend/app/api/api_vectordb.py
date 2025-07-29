from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.crud import crud_vectordb

router = APIRouter(prefix="/vectordb", tags=["VectorDB"])


@router.post("/{embedding_id}/rebuild")
async def rebuild_embedding_vector(embedding_id: int, session: AsyncSession = Depends(get_session)):
    from app.crud import crud_world_embedding
    embedding = await crud_world_embedding.get_embedding(session, embedding_id)
    if not embedding:
        raise HTTPException(status_code=404, detail="Embedding not found")
    count = await crud_vectordb.rebuild_embedding(session, embedding.world_id, embedding_id)
    return {"pages_indexed": count}


@router.post("/{embedding_id}/add_page/{page_id}")
async def add_page(embedding_id: int, page_id: int, session: AsyncSession = Depends(get_session)):
    ok = await crud_vectordb.add_page(session, page_id, embedding_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Page not found")
    return {"indexed": True}


@router.get("/{embedding_id}/search")
async def search_world(embedding_id: int, q: str = Query(..., alias="query"), n: int = 5):
    results = crud_vectordb.query_world(embedding_id, q, n_results=n)
    return results
