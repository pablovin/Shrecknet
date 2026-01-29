"""API router for Elder job execution."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from neo4j import AsyncSession as AsyncNeo4jSession
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session
from app.core.config_store import get_settings
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


async def get_llm_client():
    """Dependency to get LLM client."""
    settings = get_settings()

    if not settings.openai_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenAI API key not configured",
        )

    client = OpenAIClient(
        api_key=settings.openai_api_key,
        timeout=15,
        max_retries=2,
    )
    try:
        yield client
    finally:
        await client.aclose()


async def get_model_policy() -> ModelPolicy:
    """Dependency to get model policy."""
    settings = get_settings()

    return ModelPolicy(
        decompose_model=settings.model_decompose,
        subanswer_model=settings.model_subanswer,
        synthesis_model=settings.model_synthesis,
        validation_model=settings.model_validation,
        style_model=settings.model_style,
        architect_extract_model=getattr(
            settings, "model_architect_extract", settings.model_decompose
        ),
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
    1. Decomposes the query into sub-queries (with optional chat history context)
    2. Retrieves relevant context from Neo4j
    3. Generates sub-answers from context
    4. Synthesizes a conversational answer (with agent style)
    5. Saves query and response to chat history if chat_id provided

    Requires authentication. Returns answer and/or context based on mode.
    """
    from app.services.elder_chat_service import ElderChatService

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

    # Get chat history if chat_id is provided
    chat_history = None
    if request.chat_id:
        chat_service = ElderChatService(db_session)
        chat_history = await chat_service.get_chat_history_for_context(
            chat_id=request.chat_id, user_id=_current_user.id, limit=20
        )
        if chat_history is None:
            # Chat doesn't exist or doesn't belong to user
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Chat not found",
            )

    # Execute Elder pipeline
    try:
        logger.info(
            f"Executing Elder query for agent {agent_id} (user {_current_user.id}): {request.query[:100]}"
        )

        # Build entities hint from SQL ontology definitions (names + descriptions)
        try:
            from sqlalchemy import select
            from app.models.ontology import OntologyEntity

            if agent.ontologies:
                ontology_ids = [o.id for o in agent.ontologies]
                stmt = select(OntologyEntity).where(
                    OntologyEntity.ontology_id.in_(ontology_ids)
                )
                res = await db_session.execute(stmt)
                entities = res.scalars().all()
                lines = []
                for ent in entities[:100]:  # cap for prompt size
                    desc = (ent.description or "").strip().replace("\n", " ")
                    if len(desc) > 200:
                        desc = desc[:200] + "…"
                    lines.append(f"- {ent.name}: {desc}")
                entities_hint = "\n".join(lines) if lines else None
            else:
                entities_hint = None
        except Exception:
            entities_hint = None

        enriched_request = request.model_copy(update={"entities_hint": entities_hint})

        response = await orchestrator.execute(agent, enriched_request, chat_history)

        # Save to chat history if chat_id provided
        if request.chat_id:
            chat_service = ElderChatService(db_session)
            # Save user query (always save the question)
            await chat_service.add_message_to_chat(
                chat_id=request.chat_id,
                user_id=_current_user.id,
                role="user",
                content=request.query,
            )
            # Save assistant response (even if empty/None)
            # Build metadata with sources so the frontend can render them
            def _to_plain(item):
                """Convert pydantic models and other objects to JSON-friendly dicts."""
                if item is None:
                    return None
                try:
                    if hasattr(item, "model_dump"):
                        return item.model_dump()
                    if hasattr(item, "dict"):
                        return item.dict()
                except Exception:
                    pass
                return item

            meta: dict | None = {
                "context": [_to_plain(c) for c in (response.context or [])],
                "subanswers": [_to_plain(sa) for sa in (response.subanswers or [])],
            }

            if request.include_trace:
                subqueries: list[str] = []
                if response.trace:
                    for step in response.trace:
                        try:
                            step_name = getattr(step, "step", None) or step.get("step")
                            data = getattr(step, "data", None) or step.get("data")
                        except Exception:
                            step_name = None
                            data = None
                        if step_name == "decompose" and data and "subqueries" in data:
                            subqueries = data["subqueries"]
                            break
                meta.update(
                    {
                        "trace": [_to_plain(t) for t in (response.trace or [])],
                        "subqueries": subqueries,
                    }
                )
            await chat_service.add_message_to_chat(
                chat_id=request.chat_id,
                user_id=_current_user.id,
                role="assistant",
                content=response.answer or "",
                metadata=meta,
            )

        logger.info(f"Elder query completed for agent {agent_id}")
        return response

    except Exception as e:
        logger.error(f"Elder query failed for agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Elder query execution failed: {str(e)}",
        )
