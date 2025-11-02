"""GraphRAG API endpoints for embedding and retrieval."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_active_admin_or_world_builder
from app.graph.neo4j import get_neo4j_session
from app.graphrag.embedding_service import EMBED_DIM, EMBED_MODEL_ID, EmbeddingService
from app.graphrag.retrieval_service import RetrievalService
from app.models.user import User
from app.schemas.graphrag import (
    ContextRequest,
    ContextResponse,
    EmbedNodeRequest,
    EmbedNodeResponse,
    EmbedOntologyRequest,
    EmbedOntologyResponse,
    IndexStatusResponse,
    ResetOntologyEmbeddingsRequest,
    ResetOntologyEmbeddingsResponse,
    SemanticSearchRequest,
    SemanticSearchResponse,
)
from neo4j import AsyncSession as AsyncNeo4jSession

router = APIRouter(prefix="/graphrag", tags=["graphrag"])


@router.post("/embed/node", response_model=EmbedNodeResponse)
async def embed_node(
    request: EmbedNodeRequest,
    graph_session: AsyncNeo4jSession = Depends(get_neo4j_session),
    current_user: User = Depends(get_current_active_admin_or_world_builder),
) -> EmbedNodeResponse:
    """
    Embed a single node by ID.

    Requires admin or world_builder role.
    """
    service = EmbeddingService(graph_session)

    try:
        result = await service.embed_node(request.node_id, request.ontology_id)
        return EmbedNodeResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@router.post("/embed/ontology", response_model=EmbedOntologyResponse)
async def embed_ontology(
    request: EmbedOntologyRequest,
    graph_session: AsyncNeo4jSession = Depends(get_neo4j_session),
    current_user: User = Depends(get_current_active_admin_or_world_builder),
) -> EmbedOntologyResponse:
    """
    Embed all nodes in an ontology.

    Requires admin or world_builder role.
    This operation may take several minutes for large ontologies.
    """
    service = EmbeddingService(graph_session)

    try:
        result = await service.embed_ontology(request.ontology_id, request.batch_size)
        return EmbedOntologyResponse(**result)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@router.post("/search", response_model=SemanticSearchResponse)
async def semantic_search(
    request: SemanticSearchRequest,
    graph_session: AsyncNeo4jSession = Depends(get_neo4j_session),
) -> SemanticSearchResponse:
    """
    Perform semantic search over embedded nodes.

    Open to all authenticated users (dependency in router registration).
    """
    service = RetrievalService(graph_session)

    try:
        result = await service.semantic_search(
            query=request.query,
            ontology_id=request.ontology_id,
            k=request.k,
            score_threshold=request.score_threshold,
            include_neighbors=request.include_neighbors,
            neighbor_limit=request.neighbor_limit,
        )
        return SemanticSearchResponse(**result)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@router.post("/context", response_model=ContextResponse)
async def get_context(
    request: ContextRequest,
    graph_session: AsyncNeo4jSession = Depends(get_neo4j_session),
) -> ContextResponse:
    """
    Get formatted context for LLM from semantic search.

    Open to all authenticated users (dependency in router registration).
    """
    service = RetrievalService(graph_session)

    try:
        context = await service.get_context_for_llm(
            query=request.query,
            ontology_id=request.ontology_id,
            k=request.k,
            score_threshold=request.score_threshold,
        )
        return ContextResponse(
            query=request.query, context=context, ontology_id=request.ontology_id
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@router.post("/index/ensure", response_model=IndexStatusResponse)
async def ensure_index(
    graph_session: AsyncNeo4jSession = Depends(get_neo4j_session),
    current_user: User = Depends(get_current_active_admin_or_world_builder),
) -> IndexStatusResponse:
    """
    Ensure vector index exists in Neo4j.

    Requires admin or world_builder role.
    """
    service = EmbeddingService(graph_session)
    index_name = "entity_text_vec_idx"

    try:
        exists = await service.ensure_vector_index(index_name)
        return IndexStatusResponse(
            index_name=index_name,
            exists=exists,
            embedding_model=EMBED_MODEL_ID,
            embedding_dim=EMBED_DIM,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@router.post("/embed/ontology/backfill-chunks", response_model=EmbedOntologyResponse)
async def backfill_chunks(
    request: EmbedOntologyRequest,
    graph_session: AsyncNeo4jSession = Depends(get_neo4j_session),
    current_user: User = Depends(get_current_active_admin_or_world_builder),
) -> EmbedOntologyResponse:
    """
    Create chunk embeddings only for large-text entities within an ontology.

    Requires admin or world_builder role.
    """
    service = EmbeddingService(graph_session)
    try:
        await service.ensure_chunk_vector_index()
        result = await service.backfill_chunks(
            request.ontology_id, batch_size=request.batch_size
        )
        return EmbedOntologyResponse(**result)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@router.post(
    "/embed/ontology/reset", response_model=ResetOntologyEmbeddingsResponse
)
async def reset_embeddings(
    request: ResetOntologyEmbeddingsRequest,
    graph_session: AsyncNeo4jSession = Depends(get_neo4j_session),
    current_user: User = Depends(get_current_active_admin_or_world_builder),
) -> ResetOntologyEmbeddingsResponse:
    """
    Remove embeddings and chunk nodes for all entities in an ontology.

    Requires admin or world_builder role.
    """
    service = EmbeddingService(graph_session)
    try:
        result = await service.reset_ontology_embeddings(request.ontology_id)
        return ResetOntologyEmbeddingsResponse(**result)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )
