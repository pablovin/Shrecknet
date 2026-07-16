"""API router for Librarian job execution."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session
from app.api.agent_feature_gate import require_ai_agents_enabled
from app.core.config_store import get_settings, is_shreckllm_configured
from app.graphrag.embedding_runtime import EmbeddingRuntimeError
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.jobs.librarian.librarian import LibrarianOrchestrator
from app.jobs.librarian.schemas import LibrarianQueryRequest, LibrarianQueryResponse
from app.models.user import User
from app.repositories.agent_repository import AgentRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs/librarian", tags=["librarian"])


async def get_llm_client():
    """Dependency to get LLM client."""
    settings = get_settings()

    require_ai_agents_enabled()
    if not is_shreckllm_configured(settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="shreckLLM is not configured",
        )

    client = ShreckLLMClient(
        base_url=settings.shreckllm_base_url,
        timeout=settings.shreckllm_request_timeout_s,
        max_retries=settings.shreckllm_max_retries,
    )
    try:
        yield client
    finally:
        await client.aclose()


async def get_librarian_orchestrator(
    llm_client: ShreckLLMClient = Depends(get_llm_client),
) -> LibrarianOrchestrator:
    """Dependency to get Librarian orchestrator."""
    settings = get_settings()

    return LibrarianOrchestrator(
        llm_client=llm_client,
        answer_model=settings.model_librarian,
        repair_json_model=settings.model_agents_repair_json,
        debug_artifacts_enabled=settings.librarian_debug_artifacts_enabled,
    )


@router.post("/{agent_id}/query", response_model=LibrarianQueryResponse)
async def query_librarian(
    agent_id: str,
    request: LibrarianQueryRequest,
    _current_user: User = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
    orchestrator: LibrarianOrchestrator = Depends(get_librarian_orchestrator),
) -> LibrarianQueryResponse:
    require_ai_agents_enabled()
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

        response = await orchestrator.execute(agent, request, db_session)

        logger.info(f"Librarian query completed for agent {agent_id}")
        return response

    except EmbeddingRuntimeError as e:
        logger.error(f"Librarian embedding unavailable for agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Librarian embedding unavailable: {str(e)}",
        )
    except Exception as e:
        logger.error(f"Librarian query failed for agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Librarian query execution failed: {str(e)}",
        )
