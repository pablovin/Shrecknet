"""GraphRAG API endpoints for embedding and retrieval."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import (
    get_current_active_admin_or_world_builder,
    get_current_admin_user,
)
from app.core.config_store import get_settings
from app.graph.neo4j import get_neo4j_session
from app.graphrag.semantic_v2 import SemanticEmbeddingService
from app.integrations.retrieval.neo4j_retriever import Neo4jGraphRetriever
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
    ClearGraphResponse,
)
from neo4j import AsyncSession as AsyncNeo4jSession
from sqlalchemy.orm import Session

from app.db.session import get_session

from app.services.graph_service import GraphMaintenanceService

router = APIRouter(prefix="/graphrag", tags=["graphrag"])


@router.post("/embed/node", response_model=EmbedNodeResponse)
async def embed_node(
    request: EmbedNodeRequest,
    graph_session: AsyncNeo4jSession = Depends(get_neo4j_session),
    sql_session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_admin_or_world_builder),
) -> EmbedNodeResponse:
    """
    Embed a single node by ID.

    Requires admin or world_builder role.
    """
    service = SemanticEmbeddingService(graph_session, sql_session)

    try:
        if request.ontology_id is None:
            raise ValueError("ontology_id is required for V2 semantic embedding")
        result = await service.embed_nodes(request.ontology_id, [request.node_id])
        if not result.get("nodes_embedded"):
            raise ValueError(f"Node {request.node_id} not found")
        return EmbedNodeResponse(
            node_id=request.node_id,
            context_text="V2 semantic documents reconciled",
            embedding_model=get_settings().embedding_model_id,
            embedding_dim=get_settings().embedding_dimension,
        )
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
    sql_session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_admin_or_world_builder),
) -> EmbedOntologyResponse:
    """
    Embed all nodes in an ontology.

    Requires admin or world_builder role.
    This operation may take several minutes for large ontologies.
    """
    service = SemanticEmbeddingService(graph_session, sql_session)

    try:
        result = await service.embed_ontology(request.ontology_id, batch_size=request.batch_size)
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
    retriever = Neo4jGraphRetriever(graph_session)

    try:
        chunks = await retriever.search(
            query=request.query,
            ontology_ids=[request.ontology_id] if request.ontology_id is not None else [],
            top_k=request.k,
            node_scope=request.node_scope,
            candidate_limit=request.candidate_limit,
            rerank_limit=request.rerank_limit,
        )
        results = [
            {
                "node_id": chunk.node_id,
                "name": chunk.node_name or chunk.node_alias or chunk.node_id,
                "labels": [chunk.node_label] if chunk.node_label else [],
                "score": chunk.score,
                "context_text": chunk.text,
                "ontology_id": request.ontology_id,
                "chunk_score": chunk.chunk_score,
                "node_score": chunk.node_score,
                "importance_index": chunk.importance_index,
                "matched_chunk_count": chunk.matched_chunk_count,
                "score_breakdown": chunk.score_breakdown,
                "graph_boost": chunk.graph_boost,
                "evidence_bundle": chunk.evidence_bundle,
            }
            for chunk in chunks
        ]
        evidence_bundles = [
            bundle
            for bundle in [chunk.evidence_bundle for chunk in chunks]
            if bundle is not None
        ]
        return SemanticSearchResponse(
            query=request.query,
            results=results,
            total=len(results),
            ontology_id=request.ontology_id,
            evidence_bundles=evidence_bundles,
        )
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
    retriever = Neo4jGraphRetriever(graph_session)

    try:
        chunks = await retriever.search(
            query=request.query,
            ontology_ids=[request.ontology_id] if request.ontology_id is not None else [],
            top_k=request.k,
            node_scope=request.node_scope,
        )
        if not chunks:
            context = "No relevant information found."
        else:
            lines = [f"Query: {request.query}\n", "Relevant Information:\n"]
            for idx, chunk in enumerate(chunks, start=1):
                name = chunk.node_name or chunk.node_alias or chunk.node_id
                lines.append(f"\n{idx}. {name} (Score: {chunk.score:.2f})")
                if chunk.text:
                    lines.append(f"\n{chunk.text}")
                lines.append("\n" + "-" * 40)
            context = "\n".join(lines)
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
    # The V2 service owns both vector and full-text semantic-document indexes.
    index_name = "semantic_document_vec_idx"
    settings = get_settings()

    try:
        await graph_session.run(
            f"CREATE VECTOR INDEX {index_name} IF NOT EXISTS FOR (document:SemanticDocument) "
            "ON (document.text_embedding) OPTIONS {indexConfig: {"
            f"`vector.dimensions`: {settings.embedding_dimension}, `vector.similarity_function`: 'cosine'}}"
        )
        exists = True
        return IndexStatusResponse(
            index_name=index_name,
            exists=exists,
            embedding_model=settings.embedding_model_id,
            embedding_dim=settings.embedding_dimension,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@router.delete(
    "/admin/clear-graph",
    response_model=ClearGraphResponse,
    status_code=status.HTTP_200_OK,
)
async def clear_neo4j_graph(
    graph_session: AsyncNeo4jSession = Depends(get_neo4j_session),
    current_user: User = Depends(get_current_admin_user),
) -> ClearGraphResponse:
    """
    Delete every node and relationship from Neo4j.

    Requires admin role due to the destructive nature of this operation.
    """
    service = GraphMaintenanceService(graph_session)
    try:
        result = await service.clear_graph()
        return ClearGraphResponse(message="Neo4j graph cleared", **result)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@router.post("/embed/ontology/backfill-chunks", response_model=EmbedOntologyResponse)
async def backfill_chunks(
    request: EmbedOntologyRequest,
    graph_session: AsyncNeo4jSession = Depends(get_neo4j_session),
    sql_session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_admin_or_world_builder),
) -> EmbedOntologyResponse:
    """
    Create chunk embeddings only for large-text entities within an ontology.

    Requires admin or world_builder role.
    """
    service = SemanticEmbeddingService(graph_session, sql_session)
    try:
        result = await service.embed_ontology(request.ontology_id, batch_size=request.batch_size)
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
    sql_session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_admin_or_world_builder),
) -> ResetOntologyEmbeddingsResponse:
    """
    Remove embeddings and chunk nodes for all entities in an ontology.

    Requires admin or world_builder role.
    """
    service = SemanticEmbeddingService(graph_session, sql_session)
    try:
        result = await service.reset_ontology(request.ontology_id)
        return ResetOntologyEmbeddingsResponse(**result)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )
