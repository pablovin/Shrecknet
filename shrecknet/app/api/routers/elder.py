"""API router for Elder job execution."""

from contextlib import asynccontextmanager
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session
from app.api.agent_feature_gate import require_ai_agents_enabled
from app.core.config_store import LLMModelTarget, get_settings, is_shreckllm_configured
from app.graphrag.embedding_runtime import EmbeddingRuntimeError
from app.graph.neo4j import get_driver
from app.integrations.llm.model_policy import ModelPolicy
from app.integrations.llm.runtime_control import (
    fetch_shreckllm_runtime,
    resolve_effective_architect_concurrency,
)
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.integrations.retrieval.neo4j_retriever import (
    HybridNeo4jGraphRetriever,
    Neo4jGraphRetriever,
)
from app.jobs.elder.elder import ElderOrchestrator
from app.jobs.elder.schemas import ElderQueryRequest, ElderQueryResponse
from app.jobs.elder.v2_schemas import EvidenceCapacityError
from app.models.user import User
from app.repositories.agent_repository import AgentRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs/elder", tags=["elder"])
chat_router = APIRouter(prefix="/chat/messages", tags=["elder"])


class ChatMessageStreamRequest(ElderQueryRequest):
    agent_id: str


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


async def get_model_policy() -> ModelPolicy:
    """Dependency to get model policy."""
    settings = get_settings()

    default_target = settings.model_elder_planner
    model_policy = ModelPolicy(
        default_model=default_target,
        architect_extract_model=default_target,
    )
    setattr(model_policy, "model_elder_planner", settings.model_elder_planner)
    setattr(model_policy, "model_elder_synthesis", settings.model_elder_synthesis)
    setattr(
        model_policy,
        "model_elder_character_incorporation",
        settings.model_elder_character_incorporation,
    )
    repair_target = settings.model_agents_repair_json or default_target
    setattr(model_policy, "model_agents_repair_json", repair_target)
    return model_policy


async def get_elder_llm_concurrency() -> int:
    settings = get_settings()
    try:
        runtime_config = await fetch_shreckllm_runtime(settings)
        target = settings.model_elder_planner
        return resolve_effective_architect_concurrency(
            runtime_config,
            provider_id=target.provider,
        )
    except Exception as exc:
        logger.warning("elder_llm_concurrency_fallback_to_1 error=%s", exc)
        return 1


async def get_graph_retriever() -> HybridNeo4jGraphRetriever:
    """Dependency to get graph retriever."""
    settings = get_settings()
    driver = get_driver()

    @asynccontextmanager
    async def _session_factory():
        async with driver.session(database=settings.neo4j_database) as session:
            yield session

    return HybridNeo4jGraphRetriever(session_factory=_session_factory)


async def get_elder_orchestrator(
    llm_client: ShreckLLMClient = Depends(get_llm_client),
    model_policy: ModelPolicy = Depends(get_model_policy),
    graph_retriever: Neo4jGraphRetriever = Depends(get_graph_retriever),
    llm_max_concurrency: int = Depends(get_elder_llm_concurrency),
) -> ElderOrchestrator:
    """Dependency to get Elder orchestrator."""
    settings = get_settings()

    return ElderOrchestrator(
        llm_client=llm_client,
        model_policy=model_policy,
        graph_retriever=graph_retriever,
        llm_max_concurrency=llm_max_concurrency,
        debug_artifacts_enabled=settings.elder_debug_artifacts_enabled,
    )


@router.post("/{agent_id}/query", response_model=ElderQueryResponse)
async def query_elder(
    agent_id: str,
    request: ElderQueryRequest,
    _current_user: User = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
    orchestrator: ElderOrchestrator = Depends(get_elder_orchestrator),
) -> ElderQueryResponse:
    require_ai_agents_enabled()
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

    # Optional v2 world scope is additive and must belong to an ontology assigned
    # to this Elder. Existing clients omit it and retain all-assigned-ontology scope.
    if request.instance_id:
        from sqlalchemy import select
        from app.models.ontology_instance import OntologyInstance

        assigned_ontology_ids = {ontology.id for ontology in (agent.ontologies or [])}
        instance_result = await db_session.execute(
            select(OntologyInstance).where(OntologyInstance.instance_id == request.instance_id)
        )
        active_instance = instance_result.scalar_one_or_none()
        if active_instance is None or active_instance.ontology_id not in assigned_ontology_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="instance_id is not available to this Elder agent",
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

        # Build the complete readable ontology catalogue for v2 planning. This is
        # grounding, not evidence, and intentionally contains no lossy substrings.
        try:
            from sqlalchemy import select
            from app.models.ontology import OntologyEntity, OntologyProperty, OntologyRelationship

            if agent.ontologies:
                ontology_ids = [o.id for o in agent.ontologies]
                entity_result = await db_session.execute(
                    select(OntologyEntity).where(OntologyEntity.ontology_id.in_(ontology_ids))
                )
                entities = entity_result.scalars().all()
                entity_ids = [entity.id for entity in entities]
                property_result = await db_session.execute(
                    select(OntologyProperty).where(OntologyProperty.entity_id.in_(entity_ids))
                )
                relationship_result = await db_session.execute(
                    select(OntologyRelationship).where(
                        OntologyRelationship.entity_id.in_(entity_ids)
                    )
                )
                properties_by_entity: dict[int, list] = {}
                for prop in property_result.scalars().all():
                    properties_by_entity.setdefault(prop.entity_id, []).append(prop)
                relationships_by_entity: dict[int, list] = {}
                for relationship in relationship_result.scalars().all():
                    relationships_by_entity.setdefault(relationship.entity_id, []).append(relationship)
                definitions: list[dict] = []
                for ent in entities:
                    definitions.append(
                        {
                            "definition_id": ent.id,
                            "name": ent.name,
                            "description": ent.description or "",
                            "properties": [
                                {
                                    "property_id": prop.id,
                                    "name": prop.name,
                                    "description": prop.description or "",
                                    "data_type": prop.data_type.value,
                                    "cardinality": prop.cardinality.value,
                                }
                                for prop in properties_by_entity.get(ent.id, [])
                            ],
                            "relationships": [
                                {
                                    "relationship_definition_id": relationship.id,
                                    "name": relationship.name,
                                    "description": relationship.description or "",
                                    "target_definition_id": relationship.destiny_entity_id,
                                    "bi_directional": relationship.bi_directional,
                                }
                                for relationship in relationships_by_entity.get(ent.id, [])
                            ],
                        }
                    )
            else:
                definitions = []
        except Exception:
            definitions = []

        enriched_request = request.model_copy(update={"grounding_definitions": definitions})

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
                "sources": [_to_plain(s) for s in (response.sources or [])],
                "timings": _to_plain(response.timings),
                "retrieval_plan": _to_plain(response.retrieval_plan),
                "memory_priors_applied": _to_plain(response.memory_priors_applied),
                "trace_id": response.trace_id,
                "pipeline_version": response.pipeline_version,
                "llm_usage": _to_plain(response.llm_usage),
                "llm_usage_totals": _to_plain(response.llm_usage_totals),
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
                        if step_name == "retrieval_plan" and data:
                            subqueries = [
                                str(item.get("query") or "")
                                for item in data.get("steps") or []
                                if isinstance(item, dict) and item.get("query")
                            ]
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

    except EmbeddingRuntimeError as e:
        logger.error(f"Elder embedding unavailable for agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Elder embedding unavailable: {str(e)}",
        )
    except EvidenceCapacityError as e:
        logger.warning(
            "Elder evidence exceeds model capacity agent_id=%s evidence_id=%s required=%s available=%s",
            agent_id,
            e.evidence_id,
            e.required_tokens,
            e.available_tokens,
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "code": "elder_evidence_capacity_exceeded",
                "evidence_id": e.evidence_id,
                "required_tokens": e.required_tokens,
                "available_tokens": e.available_tokens,
            },
        )
    except Exception as e:
        logger.error(f"Elder query failed for agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Elder query execution failed: {str(e)}",
        )


@chat_router.post("/stream", response_model=ElderQueryResponse)
async def stream_chat_messages(
    request: ChatMessageStreamRequest,
    current_user: User = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
    orchestrator: ElderOrchestrator = Depends(get_elder_orchestrator),
) -> ElderQueryResponse:
    elder_request = ElderQueryRequest.model_validate(request.model_dump(exclude={"agent_id"}))
    return await query_elder(
        agent_id=request.agent_id,
        request=elder_request,
        _current_user=current_user,
        db_session=db_session,
        orchestrator=orchestrator,
    )
