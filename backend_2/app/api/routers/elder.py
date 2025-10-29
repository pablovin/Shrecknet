"""API router for Elder job execution."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from neo4j import AsyncSession as AsyncNeo4jSession
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session
from app.core.config import get_settings
from app.graph.neo4j import get_neo4j_session
from app.integrations.llm.model_policy import ModelPolicy
from app.integrations.llm.openai_client import OpenAIClient
from app.integrations.retrieval.neo4j_retriever import Neo4jGraphRetriever
from app.jobs.elder.elder import ElderOrchestrator
from app.jobs.elder.schemas import ElderQueryRequest, ElderQueryResponse
from app.models.user import User
from app.repositories.agent_repository import AgentRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs/elder", tags=["elder"])


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


async def get_model_policy() -> ModelPolicy:
    """Dependency to get model policy."""
    settings = get_settings()

    return ModelPolicy(
        decompose_model=settings.model_decompose,
        subanswer_model=settings.model_subanswer,
        synthesis_model=settings.model_synthesis,
        validation_model=settings.model_validation,
        style_model=settings.model_style,
    )


async def get_graph_retriever(
    graph_session: AsyncNeo4jSession = Depends(get_neo4j_session),
) -> Neo4jGraphRetriever:
    """Dependency to get graph retriever."""
    return Neo4jGraphRetriever(graph_session)


async def get_elder_orchestrator(
    llm_client: OpenAIClient = Depends(get_llm_client),
    model_policy: ModelPolicy = Depends(get_model_policy),
    graph_retriever: Neo4jGraphRetriever = Depends(get_graph_retriever),
) -> ElderOrchestrator:
    """Dependency to get Elder orchestrator."""
    settings = get_settings()

    return ElderOrchestrator(
        llm_client=llm_client,
        model_policy=model_policy,
        graph_retriever=graph_retriever,
        default_top_k=settings.default_top_k,
    )


@router.post("/{agent_id}/query", response_model=ElderQueryResponse)
async def query_elder(
    agent_id: str,
    request: ElderQueryRequest,
    _current_user: User = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
    orchestrator: ElderOrchestrator = Depends(get_elder_orchestrator),
) -> ElderQueryResponse:
    """
    Execute an Elder query through an agent.

    The Elder pipeline:
    1. Decomposes the query into sub-queries
    2. Retrieves relevant context from Neo4j
    3. Generates sub-answers from context
    4. Synthesizes a final answer
    5. Validates and optionally refines the answer
    6. Applies agent writing style if configured

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

    # Check if agent has the elder job type
    if agent.job != "elder":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Agent job type '{agent.job}' is not 'elder'",
        )

    # Execute Elder pipeline
    try:
        logger.info(
            f"Executing Elder query for agent {agent_id} (user {_current_user.id}): {request.query[:100]}"
        )

        response = await orchestrator.execute(agent, request)

        logger.info(f"Elder query completed for agent {agent_id}")
        return response

    except Exception as e:
        logger.error(f"Elder query failed for agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Elder query execution failed: {str(e)}",
        )
