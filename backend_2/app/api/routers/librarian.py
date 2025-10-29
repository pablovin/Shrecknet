"""API router for Librarian job execution."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from neo4j import AsyncSession as AsyncNeo4jSession
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session
from app.core.config import get_settings
from app.graph.neo4j import get_neo4j_session
from app.integrations.llm.openai_client import OpenAIClient
from app.jobs.librarian.librarian import LibrarianOrchestrator
from app.jobs.librarian.schemas import LibrarianQueryRequest, LibrarianQueryResponse
from app.models.user import User
from app.repositories.agent_repository import AgentRepository
from app.services.pdf_embedding_service import PdfEmbeddingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs/librarian", tags=["librarian"])


async def get_llm_client() -> OpenAIClient:
    """Dependency to get LLM client."""
    settings = get_settings()

    if not settings.openai_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenAI API key not configured",
        )

    return OpenAIClient(
        api_key=settings.openai_api_key,
        timeout=60,
        max_retries=3,
    )


async def get_pdf_embedding_service(
    graph_session: AsyncNeo4jSession = Depends(get_neo4j_session),
) -> PdfEmbeddingService:
    """Dependency to get PDF embedding service."""
    return PdfEmbeddingService(graph_session)


async def get_librarian_orchestrator(
    llm_client: OpenAIClient = Depends(get_llm_client),
    pdf_embedding_service: PdfEmbeddingService = Depends(get_pdf_embedding_service),
) -> LibrarianOrchestrator:
    """Dependency to get Librarian orchestrator."""
    settings = get_settings()

    return LibrarianOrchestrator(
        llm_client=llm_client,
        pdf_embedding_service=pdf_embedding_service,
        default_top_k=settings.default_top_k,
        answer_model=settings.model_synthesis,  # Reuse synthesis model for answers
        style_model=settings.model_style,
    )


@router.post("/{agent_id}/query", response_model=LibrarianQueryResponse)
async def query_librarian(
    agent_id: str,
    request: LibrarianQueryRequest,
    _current_user: User = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
    orchestrator: LibrarianOrchestrator = Depends(get_librarian_orchestrator),
) -> LibrarianQueryResponse:
    """
    Execute a Librarian query through an agent.

    The Librarian pipeline:
    1. Retrieves relevant chunks from embedded PDF books
    2. Generates an answer based on the retrieved content
    3. Applies agent writing style if configured

    Requires authentication. Returns answer and/or context based on mode.
    """
    # Get agent
    agent_repo = AgentRepository(db_session)
    agent = await agent_repo.get_by_id(agent_id)

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found",
        )

    # Check if agent is active
    if not agent.active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Agent is not active",
        )

    # Check if agent has the librarian job type
    if agent.job != "librarian":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Agent job type '{agent.job}' is not 'librarian'",
        )

    # Execute Librarian pipeline
    try:
        logger.info(
            f"Executing Librarian query for agent {agent_id} "
            f"(user {_current_user.id}): {request.query[:100]}"
        )

        response = await orchestrator.execute(agent, request)

        logger.info(f"Librarian query completed for agent {agent_id}")
        return response

    except Exception as e:
        logger.error(f"Librarian query failed for agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Librarian query execution failed: {str(e)}",
        )
