"""Background task for Companion Herald Orchestrator turns."""

from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from app.celery_app import celery_app
from app.core.config_store import LLMModelTarget, get_settings
from app.db.session import AsyncSessionMaker
from app.graph.neo4j import get_driver
from app.integrations.llm.model_policy import ModelPolicy
from app.integrations.llm.runtime_control import (
    fetch_shreckllm_runtime,
)
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.integrations.retrieval.neo4j_retriever import HybridNeo4jGraphRetriever, Neo4jGraphRetriever
from app.jobs.elder.elder import ElderOrchestrator
from app.jobs.elder.schemas import ElderQueryRequest
from app.jobs.librarian.librarian import LibrarianOrchestrator
from app.jobs.librarian.schemas import LibrarianQueryRequest
from app.jobs.personal_companion.personal_companion_orchestrator import (
    PersonalCompanionOrchestrator,
)
from app.repositories.agent_repository import AgentRepository
from app.repositories.personal_companion_agent_repository import (
    PersonalCompanionAgentRepository,
)
from app.utils.async_helpers import run_async
from app.utils.companion_orchestrator_store import append_chat_message, get_session
from app.utils.job_tracking import (
    mark_job_done,
    mark_job_failed,
    mark_job_running,
    update_job_progress,
)

logger = logging.getLogger(__name__)


def _log_companion_agent_trace(*, job: str, agent_id: str, agent_name: str | None, trace: Any) -> None:
    if not trace:
        logger.info(
            "companion_agent_trace job=%s agent_id=%s agent_name=%s trace=[]",
            job,
            agent_id,
            agent_name,
        )
        return
    try:
        payload = json.dumps(trace, ensure_ascii=True, default=str)
    except Exception:
        payload = repr(trace)
    logger.info(
        "companion_agent_trace job=%s agent_id=%s agent_name=%s trace=%s",
        job,
        agent_id,
        agent_name,
        payload,
    )


def _build_model_policy(default_target) -> ModelPolicy:
    settings = get_settings()
    policy = ModelPolicy(default_model=default_target, architect_extract_model=default_target)
    setattr(policy, "model_elder", settings.model_elder)
    setattr(policy, "model_agents_repair_json", settings.model_agents_repair_json)
    return policy


async def _run_elder_agent(
    *,
    llm_client: ShreckLLMClient,
    graph_retriever: Neo4jGraphRetriever,
    model_policy: ModelPolicy,
    agent_id: str,
    query: str,
) -> dict[str, Any]:
    started = time.monotonic()
    settings = get_settings()
    async with AsyncSessionMaker() as sql_session:
        repo = AgentRepository(sql_session)
        agent = await repo.get_by_id(agent_id)
    if agent is None:
        logger.warning("companion_agent_missing job=elder agent_id=%s", agent_id)
        return {
            "agent_id": agent_id,
            "agent_job": "elder",
            "ok": False,
            "error": "agent_not_found",
        }
    if agent.job != "elder":
        logger.warning(
            "companion_agent_invalid_job expected=elder actual=%s agent_id=%s agent_name=%s",
            agent.job,
            agent.id,
            agent.name,
        )
        return {
            "agent_id": agent.id,
            "agent_name": agent.name,
            "agent_job": agent.job,
            "ok": False,
            "error": "invalid_job_type",
        }

    orchestrator = ElderOrchestrator(
        llm_client=llm_client,
        model_policy=model_policy,
        graph_retriever=graph_retriever,
        default_top_k=settings.default_top_k,
        llm_max_concurrency=2,
        debug_artifacts_enabled=settings.elder_debug_artifacts_enabled,
    )
    request = ElderQueryRequest(
        query=query,
        mode="both",
        include_trace=settings.companion_agent_trace_enabled,
        route="auto",
    )
    try:
        logger.info(
            "companion_agent_start job=elder agent_id=%s agent_name=%s query_chars=%s",
            agent.id,
            agent.name,
            len(query or ""),
        )
        response = await orchestrator.execute(agent, request, None)
        if settings.companion_agent_trace_enabled:
            _log_companion_agent_trace(
                job="elder",
                agent_id=agent.id,
                agent_name=agent.name,
                trace=response.trace,
            )
        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
        logger.info(
            "companion_agent_done job=elder agent_id=%s agent_name=%s sources=%s answer_chars=%s elapsed_ms=%s",
            agent.id,
            agent.name,
            len(response.sources),
            len(response.answer or ""),
            elapsed_ms,
        )
        return {
            "agent_id": agent.id,
            "agent_name": agent.name,
            "agent_job": "elder",
            "ok": True,
            "answer": response.answer,
            "sources": [source.model_dump() for source in response.sources],
            "trace": response.trace if settings.companion_agent_trace_enabled else None,
            "timings": response.timings,
            "llm_usage": response.llm_usage,
            "llm_usage_totals": response.llm_usage_totals,
        }
    except Exception as exc:
        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
        logger.warning(
            "companion_agent_failed job=elder agent_id=%s agent_name=%s elapsed_ms=%s error=%s",
            agent.id,
            agent.name,
            elapsed_ms,
            exc,
            exc_info=True,
        )
        return {
            "agent_id": agent.id,
            "agent_name": agent.name,
            "agent_job": "elder",
            "ok": False,
            "error": str(exc),
        }


async def _run_librarian_agent(
    *,
    llm_client: ShreckLLMClient,
    driver,
    agent_id: str,
    query: str,
) -> dict[str, Any]:
    started = time.monotonic()
    settings = get_settings()
    async with AsyncSessionMaker() as sql_session:
        repo = AgentRepository(sql_session)
        agent = await repo.get_by_id(agent_id)
        if agent is None:
            logger.warning("companion_agent_missing job=librarian agent_id=%s", agent_id)
            return {
                "agent_id": agent_id,
                "agent_job": "librarian",
                "ok": False,
                "error": "agent_not_found",
            }
        if agent.job != "librarian":
            logger.warning(
                "companion_agent_invalid_job expected=librarian actual=%s agent_id=%s agent_name=%s",
                agent.job,
                agent.id,
                agent.name,
            )
            return {
                "agent_id": agent.id,
                "agent_name": agent.name,
                "agent_job": agent.job,
                "ok": False,
                "error": "invalid_job_type",
            }
        async with driver.session(database=settings.neo4j_database) as graph_session:
            orchestrator = LibrarianOrchestrator(
                llm_client=llm_client,
                answer_model=settings.model_librarian,
                repair_json_model=settings.model_agents_repair_json,
                debug_artifacts_enabled=settings.librarian_debug_artifacts_enabled,
            )
            request = LibrarianQueryRequest(
                query=query,
                mode="both",
                include_trace=settings.companion_agent_trace_enabled,
            )
            try:
                logger.info(
                    "companion_agent_start job=librarian agent_id=%s agent_name=%s query_chars=%s",
                    agent.id,
                    agent.name,
                    len(query or ""),
                )
                response = await orchestrator.execute(agent, request, sql_session)
                if settings.companion_agent_trace_enabled:
                    _log_companion_agent_trace(
                        job="librarian",
                        agent_id=agent.id,
                        agent_name=agent.name,
                        trace=response.trace,
                    )
                elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                logger.info(
                    "companion_agent_done job=librarian agent_id=%s agent_name=%s sources=%s library_items=%s answer_chars=%s elapsed_ms=%s",
                    agent.id,
                    agent.name,
                    len(response.sources_used),
                    len(response.library_items_used),
                    len(response.answer or ""),
                    elapsed_ms,
                )
                return {
                    "agent_id": agent.id,
                    "agent_name": agent.name,
                    "agent_job": "librarian",
                    "ok": True,
                    "answer": response.answer,
                    "sources": [chunk.model_dump() for chunk in response.sources_used],
                    "library_items_used": response.library_items_used,
                    "trace": response.trace if settings.companion_agent_trace_enabled else None,
                }
            except Exception as exc:
                elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                logger.warning(
                    "companion_agent_failed job=librarian agent_id=%s agent_name=%s elapsed_ms=%s error=%s",
                    agent.id,
                    agent.name,
                    elapsed_ms,
                    exc,
                    exc_info=True,
                )
                return {
                    "agent_id": agent.id,
                    "agent_name": agent.name,
                    "agent_job": "librarian",
                    "ok": False,
                    "error": str(exc),
                }


async def _run_turn(
    *,
    job_id: int,
    user_id: int,
    session_id: str,
    query: str,
) -> dict[str, Any]:
    turn_started = time.monotonic()
    settings = get_settings()
    orchestrator = PersonalCompanionOrchestrator(llm_client=ShreckLLMClient(
        base_url=settings.shreckllm_base_url,
        timeout=settings.shreckllm_request_timeout_s,
        max_retries=settings.shreckllm_max_retries,
    ))
    session_payload = get_session(user_id, session_id)
    if session_payload is None:
        raise ValueError("Companion orchestrator session not found")

    allocated_tools = session_payload.get("allocated_tools") or {}
    elder_agents = allocated_tools.get("elder") or []
    librarian_agents = allocated_tools.get("librarian") or []
    logger.info(
        "companion_turn_start job_id=%s user_id=%s session_id=%s query_chars=%s allocated_elder=%s allocated_librarian=%s",
        job_id,
        user_id,
        session_id,
        len(query or ""),
        len(elder_agents),
        len(librarian_agents),
    )

    await update_job_progress(
        job_id,
        0.15,
        {
            "status": "routing",
            "session_id": session_id,
            "query": query,
            "allocated_tools": allocated_tools,
        },
    )

    llm_client = orchestrator.llm_client
    driver = get_driver()

    try:
        route_started = time.monotonic()
        logger.info("companion_turn_step job_id=%s step=route status=start", job_id)
        routing = await orchestrator.route_query(query)
        logger.info(
            "companion_turn_step job_id=%s step=route status=done use_elder=%s use_librarian=%s reason=%s elapsed_ms=%s",
            job_id,
            routing.get("use_elder"),
            routing.get("use_librarian"),
            routing.get("reason"),
            round((time.monotonic() - route_started) * 1000, 2),
        )

        selected_elder_ids = [
            str(item.get("id"))
            for item in elder_agents
            if item.get("id") and routing.get("use_elder")
        ]
        selected_librarian_ids = [
            str(item.get("id"))
            for item in librarian_agents
            if item.get("id") and routing.get("use_librarian")
        ]
        if not selected_elder_ids and not selected_librarian_ids and elder_agents:
            selected_elder_ids = [str(item.get("id")) for item in elder_agents if item.get("id")]
        logger.info(
            "companion_turn_step job_id=%s step=select_tools elder=%s librarian=%s fallback_elder=%s",
            job_id,
            selected_elder_ids,
            selected_librarian_ids,
            bool(selected_elder_ids and not routing.get("use_elder") and not selected_librarian_ids),
        )

        await update_job_progress(
            job_id,
            0.45,
            {
                "status": "querying_agents",
                "routing": routing,
                "selected_tools": {
                    "elder": selected_elder_ids,
                    "librarian": selected_librarian_ids,
                },
            },
        )

        runtime_config = {}
        try:
            runtime_config = await fetch_shreckllm_runtime(settings)
            logger.info("companion_turn_step job_id=%s step=runtime_config status=loaded", job_id)
        except Exception:
            runtime_config = {}
            logger.warning("companion_turn_step job_id=%s step=runtime_config status=failed", job_id, exc_info=True)
        default_target = settings.model_orchestrator_routing or LLMModelTarget(provider="openai", name="gpt-5-nano")
        logger.info(
            "companion_turn_step job_id=%s step=model_policy default_provider=%s default_model=%s",
            job_id,
            getattr(default_target, "provider", None),
            getattr(default_target, "name", default_target),
        )
        model_policy = _build_model_policy(default_target)

        @asynccontextmanager
        async def _graph_session_factory():
            async with driver.session(database=settings.neo4j_database) as graph_session:
                yield graph_session

        graph_retriever = HybridNeo4jGraphRetriever(session_factory=_graph_session_factory)

        async def _elder_runner(agent_id: str) -> dict[str, Any]:
            return await _run_elder_agent(
                llm_client=llm_client,
                graph_retriever=graph_retriever,
                model_policy=model_policy,
                agent_id=agent_id,
                query=query,
            )

        async def _librarian_runner(agent_id: str) -> dict[str, Any]:
            return await _run_librarian_agent(
                llm_client=llm_client,
                driver=driver,
                agent_id=agent_id,
                query=query,
            )

        fanout_started = time.monotonic()
        logger.info(
            "companion_turn_step job_id=%s step=fanout status=start elder_count=%s librarian_count=%s",
            job_id,
            len(selected_elder_ids),
            len(selected_librarian_ids),
        )
        agent_responses = await orchestrator.fanout_tools(
            selected_elder_ids=selected_elder_ids,
            selected_librarian_ids=selected_librarian_ids,
            elder_runner=_elder_runner,
            librarian_runner=_librarian_runner,
        )
        ok_count = len([item for item in agent_responses if item.get("ok")])
        logger.info(
            "companion_turn_step job_id=%s step=fanout status=done responses=%s ok=%s failed=%s elapsed_ms=%s",
            job_id,
            len(agent_responses),
            ok_count,
            len(agent_responses) - ok_count,
            round((time.monotonic() - fanout_started) * 1000, 2),
        )

        async with AsyncSessionMaker() as sql_session:
            companion_repo = PersonalCompanionAgentRepository(sql_session)
            companion = await companion_repo.get_by_user_id(user_id)

        companion_name = companion.name if companion else "Companion"
        companion_writing_style = (
            companion.writing_style if companion and companion.writing_style else "clear and helpful"
        )

        await update_job_progress(
            job_id,
            0.8,
            {
                "status": "synthesizing",
                "agent_response_count": len(agent_responses),
            },
        )

        synth_started = time.monotonic()
        logger.info(
            "companion_turn_step job_id=%s step=synthesis status=start agent_response_count=%s",
            job_id,
            len(agent_responses),
        )
        final_text = await orchestrator.synthesize_final_answer(
            query=query,
            companion_name=companion_name,
            companion_writing_style=companion_writing_style,
            agent_responses=agent_responses,
        )
        logger.info(
            "companion_turn_step job_id=%s step=synthesis status=done final_chars=%s elapsed_ms=%s",
            job_id,
            len(final_text or ""),
            round((time.monotonic() - synth_started) * 1000, 2),
        )

        payload = orchestrator.build_turn_payload(
            session_id=session_id,
            query=query,
            routing=routing,
            selected_tools={
                "elder": selected_elder_ids,
                "librarian": selected_librarian_ids,
            },
            agent_responses=agent_responses,
            final_text=final_text,
        )
        logger.info(
            "companion_turn_done job_id=%s session_id=%s sources=%s claims=%s tool_failures=%s elapsed_ms=%s",
            job_id,
            session_id,
            len(payload.get("sources") or []),
            len(payload.get("claims") or []),
            len(payload.get("tool_failures") or []),
            round((time.monotonic() - turn_started) * 1000, 2),
        )
        return payload
    finally:
        await llm_client.aclose()


@celery_app.task(name="companion.orchestrate_turn")
def run_companion_orchestrator_turn(
    *,
    job_id: int,
    user_id: int,
    session_id: str,
    query: str,
) -> dict[str, Any]:
    """Execute a world-scoped companion turn through the Herald orchestrator."""
    task_started = time.monotonic()
    try:
        logger.info(
            "companion_task_start job_id=%s user_id=%s session_id=%s query_chars=%s",
            job_id,
            user_id,
            session_id,
            len(query or ""),
        )
        run_async(mark_job_running(job_id))
        run_async(
            update_job_progress(
                job_id,
                0.05,
                {
                    "status": "world_resolved",
                    "session_id": session_id,
                    "query": query,
                },
            )
        )
        payload = run_async(
            _run_turn(job_id=job_id, user_id=user_id, session_id=session_id, query=query)
        )
        try:
            session_payload = get_session(user_id, session_id) or {}
            companion_id = str(session_payload.get("companion_id") or "")
            if companion_id:
                final_block = payload.get("final") if isinstance(payload, dict) else {}
                final_content = (
                    str((final_block or {}).get("annotated_text") or "").strip()
                    or str((final_block or {}).get("text") or "").strip()
                    or "I couldn't produce a response."
                )
                append_chat_message(
                    user_id=user_id,
                    companion_id=companion_id,
                    session_id=session_id,
                    role="assistant",
                    content=final_content,
                    metadata={
                        "job_id": job_id,
                        "status": "done",
                        "routing": payload.get("routing") if isinstance(payload, dict) else None,
                        "selected_tools": payload.get("selected_tools") if isinstance(payload, dict) else None,
                        "claims": payload.get("claims") if isinstance(payload, dict) else None,
                        "analysis": payload.get("analysis") if isinstance(payload, dict) else None,
                        "sources": payload.get("sources") if isinstance(payload, dict) else None,
                        "tool_failures": payload.get("tool_failures") if isinstance(payload, dict) else None,
                    },
                )
        except Exception:
            logger.warning(
                "companion_chat_append_failed job_id=%s session_id=%s",
                job_id,
                session_id,
                exc_info=True,
            )
        run_async(mark_job_done(job_id, payload))
        logger.info(
            "companion_task_done job_id=%s session_id=%s elapsed_ms=%s",
            job_id,
            session_id,
            round((time.monotonic() - task_started) * 1000, 2),
        )
        return {"job_id": job_id, "status": "success"}
    except Exception as exc:
        logger.error(
            "companion orchestrator turn failed job_id=%s session_id=%s: %s",
            job_id,
            session_id,
            exc,
            exc_info=True,
        )
        run_async(
            mark_job_failed(
                job_id,
                str(exc),
                {
                    "status": "failed",
                    "session_id": session_id,
                    "query": query,
                },
            )
        )
        try:
            session_payload = get_session(user_id, session_id) or {}
            companion_id = str(session_payload.get("companion_id") or "")
            if companion_id:
                append_chat_message(
                    user_id=user_id,
                    companion_id=companion_id,
                    session_id=session_id,
                    role="assistant",
                    content=(
                        "I couldn't complete this request due to an internal error. "
                        "Please try again."
                    ),
                    metadata={
                        "job_id": job_id,
                        "status": "failed",
                        "error": str(exc),
                    },
                )
        except Exception:
            logger.warning(
                "companion_chat_append_failed_on_error job_id=%s session_id=%s",
                job_id,
                session_id,
                exc_info=True,
            )
        raise
