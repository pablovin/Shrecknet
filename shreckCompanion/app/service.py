from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import UploadFile

from app.core.config import Settings
from app.integrations.clients import ShreckLLMClient, ShrecknetProviderClient
from app.jobs.herald_orchestrator import HeraldOrchestrator
from app.media import CompanionMediaService
from app.schemas import (
    CompanionUserRapportRead,
    CompanionChatSessionCount,
    CompanionChatSessionCreateRequest,
    CompanionChatSessionRead,
    CompanionChatSessionUpdateRequest,
    CompanionOrchestratorTurnQueuedResponse,
    CompanionWorldBootstrapResponse,
    PersonalCompanionAgentCreate,
    PersonalCompanionAgentRead,
    PersonalCompanionAgentUpdate,
    ServiceStatusResponse,
)
from app.persistence.store import CompanionStore

logger = logging.getLogger(__name__)


class CompanionService:
    TURN_PHASES: tuple[str, ...] = ("policy", "planning", "selecting_tools", "executing_steps", "synthesizing", "reflection")

    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = CompanionStore(settings)
        self.llm_client = ShreckLLMClient(settings)
        self.provider_client = ShrecknetProviderClient(settings)
        self.media_service = CompanionMediaService(settings)
        self.orchestrator = HeraldOrchestrator(settings, self.llm_client, self.provider_client)
        self._tasks: set[asyncio.Task] = set()

    @staticmethod
    def _debug_text(value: Any, *, limit: int = 4000) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return f"{text[:limit]}... [truncated {len(text) - limit} chars]"

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await self.llm_client.aclose()
        await self.provider_client.aclose()

    async def health(self) -> dict[str, object]:
        return {"status": "ok", "service": "ShreckCompanion"}

    async def ready(self) -> dict[str, object]:
        return {
            "status": "ready",
            "service": "ShreckCompanion",
            "database_path": str(self.settings.database_path),
        }

    async def status(self) -> ServiceStatusResponse:
        return ServiceStatusResponse(
            service="ShreckCompanion",
            status="ready",
            database_path=str(self.settings.database_path),
            shreckllm_base_url=self.settings.shreckllm_base_url,
            shrecknet_api_base_url=self.settings.shrecknet_api_base_url,
            active_jobs=sum(1 for task in self._tasks if not task.done()),
        )

    def config_public_view(self) -> dict[str, object]:
        return {
            "service": "ShreckCompanion",
            "data_dir": self.settings.data_dir,
            "media_root": self.settings.media_root,
            "media_base_url": self.settings.media_base_url,
            "host": self.settings.host,
            "port": self.settings.port,
            "shreckllm_base_url": self.settings.shreckllm_base_url,
            "shrecknet_api_base_url": self.settings.shrecknet_api_base_url,
            "default_user_id": self.settings.default_user_id,
            "model_personal_companion_routing": self.settings.model_personal_companion_routing.model_dump(),
            "model_personal_companion_synthesis": self.settings.model_personal_companion_synthesis.model_dump(),
            "model_personal_companion_policy": self.settings.model_personal_companion_policy.model_dump(),
            "model_personal_companion_reflection": self.settings.model_personal_companion_reflection.model_dump(),
            "routing_temperature": self.settings.routing_temperature,
            "synthesis_temperature": self.settings.synthesis_temperature,
            "policy_temperature": self.settings.policy_temperature,
            "reflection_temperature": self.settings.reflection_temperature,
            "turn_query_max_length": self.settings.turn_query_max_length,
            "provider_timeout_seconds": self.settings.provider_timeout_seconds,
            "turn_job_result_ttl_seconds": self.settings.turn_job_result_ttl_seconds,
            "rapport_confidence_threshold": self.settings.rapport_confidence_threshold,
            "rapport_max_trait_delta_per_turn": self.settings.rapport_max_trait_delta_per_turn,
        }

    def frontend_config_view(self) -> dict[str, object]:
        return {
            "service": "ShreckCompanion",
            "version": 1,
            "models": {
                "personal_companion_routing": self.settings.model_personal_companion_routing.model_dump(),
                "personal_companion_synthesis": self.settings.model_personal_companion_synthesis.model_dump(),
                "personal_companion_policy": self.settings.model_personal_companion_policy.model_dump(),
                "personal_companion_reflection": self.settings.model_personal_companion_reflection.model_dump(),
            },
            "media": {
                "base_url": self.settings.media_base_url,
                "companion_avatar_pattern": f"{self.settings.media_base_url.rstrip('/')}/{{username}}/companion.png",
            },
            "headers": {
                "authorization": "Authorization",
                "user_id": "X-Shreck-User-Id",
                "username": "X-Shreck-Username",
            },
            "endpoints": {
                "health": "/health",
                "ready": "/ready",
                "status": "/status",
                "config": "/config",
                "frontend_config": "/config/frontend",
                "companion": "/users/me/companion",
                "companion_rapport": "/users/me/companion/rapport",
                "bootstrap": "/users/me/companion/orchestrator/bootstrap",
                "list_chats": "/users/me/companion/orchestrator/chats?ontology_id={ontology_id}",
                "create_chat": "/users/me/companion/orchestrator/chats",
                "chat_detail": "/users/me/companion/orchestrator/chats/{session_id}",
                "chat_counts": "/users/me/companion/orchestrator/chat-counts",
                "queue_turn": "/users/me/companion/orchestrator/chats/{session_id}/turns",
                "turn_result": "/users/me/companion/orchestrator/turns/{job_id}",
                "chat_file": "/users/me/companion/orchestrator/chats/{session_id}/file",
                "companion_avatar": "/users/me/companion/avatar",
            },
            "limits": {
                "turn_query_max_length": self.settings.turn_query_max_length,
                "turn_job_result_ttl_seconds": self.settings.turn_job_result_ttl_seconds,
                "provider_timeout_seconds": self.settings.provider_timeout_seconds,
                "max_image_upload_bytes": self.settings.max_image_upload_bytes,
                "image_max_width": self.settings.image_max_width,
                "image_max_height": self.settings.image_max_height,
                "rapport_confidence_threshold": self.settings.rapport_confidence_threshold,
                "rapport_max_trait_delta_per_turn": self.settings.rapport_max_trait_delta_per_turn,
            },
        }

    def create_companion(self, user_id: int, payload: PersonalCompanionAgentCreate) -> PersonalCompanionAgentRead:
        return self.store.create_companion(user_id, payload)

    def get_companion(self, user_id: int) -> PersonalCompanionAgentRead:
        return self.store.get_companion(user_id)

    def update_companion(self, user_id: int, payload: PersonalCompanionAgentUpdate) -> PersonalCompanionAgentRead:
        return self.store.update_companion(user_id, payload)

    def get_companion_rapport(self, user_id: int) -> CompanionUserRapportRead:
        companion = self.get_companion(user_id)
        rapport = self.store.get_or_create_rapport_profile(user_id=user_id, companion_id=companion.id)
        return CompanionUserRapportRead(
            user_id=user_id,
            companion_id=companion.id,
            adaptive_traits={
                str(key): float(value)
                for key, value in (rapport.get("adaptive_traits") or {}).items()
            },
            observed_preferences=[str(item) for item in (rapport.get("observed_preferences") or [])],
            negative_signals=[str(item) for item in (rapport.get("negative_signals") or [])],
            recent_user_state=dict(rapport.get("recent_user_state") or {}),
            updated_at=datetime.fromisoformat(str(rapport.get("updated_at"))),
        )

    async def upload_companion_avatar(
        self,
        *,
        user_id: int,
        username: str | None,
        file: UploadFile,
    ) -> PersonalCompanionAgentRead:
        self.get_companion(user_id)
        avatar_url = await self.media_service.save_companion_avatar(file, username=username or "", user_id=user_id)
        return self.store.update_companion(user_id, PersonalCompanionAgentUpdate(avatar_url=avatar_url))

    def delete_companion(self, user_id: int) -> None:
        self.store.delete_companion(user_id)

    async def bootstrap_world(
        self,
        *,
        user_id: int,
        ontology_id: int,
        auth_header: str | None = None,
    ) -> CompanionWorldBootstrapResponse:
        logger.info("companion bootstrap started user_id=%s ontology_id=%s", user_id, ontology_id)
        companion = self.get_companion(user_id)
        logger.info("companion bootstrap allocating tools user_id=%s ontology_id=%s", user_id, ontology_id)
        allocated = await self.provider_client.allocate_tools(
            user_id=user_id,
            ontology_id=ontology_id,
            auth_header=auth_header,
        )
        logger.info(
            "companion bootstrap tools allocated user_id=%s ontology_id=%s elder=%s librarian=%s",
            user_id,
            ontology_id,
            len(allocated.elder),
            len(allocated.librarian),
        )
        return CompanionWorldBootstrapResponse(
            companion_id=companion.id,
            ontology_id=ontology_id,
            allocated_tools=allocated,
            existing_chat_count=len(self.store.list_sessions(user_id, ontology_id=ontology_id, companion_id=companion.id)),
            chat_limit=int(self.settings.companion_chat_session_limit_per_ontology),
        )

    def list_chat_sessions(self, *, user_id: int, ontology_id: int) -> list[CompanionChatSessionRead]:
        companion = self.get_companion(user_id)
        sessions = self.store.list_sessions(user_id, ontology_id=ontology_id, companion_id=companion.id)
        output: list[CompanionChatSessionRead] = []
        for session in sessions:
            chat_payload = self.store.read_chat_file(user_id, companion.id, str(session["session_id"])) or {}
            output.append(
                CompanionChatSessionRead(
                    session_id=str(session["session_id"]),
                    companion_id=companion.id,
                    ontology_id=int(session["ontology_id"]),
                    title=str(session["title"] or "New chat"),
                    message_count=len(chat_payload.get("messages") or []),
                    created_at=session["created_at"],
                    updated_at=session["updated_at"],
                    last_message_at=session.get("last_message_at"),
                )
            )
        return output

    async def create_chat_session(
        self,
        *,
        user_id: int,
        payload: CompanionChatSessionCreateRequest,
        auth_header: str | None = None,
    ) -> CompanionChatSessionRead:
        companion = self.get_companion(user_id)
        allocated = await self.provider_client.allocate_tools(
            user_id=user_id,
            ontology_id=payload.ontology_id,
            auth_header=auth_header,
        )
        session = self.store.create_session(
            user_id=user_id,
            companion_id=companion.id,
            ontology_id=payload.ontology_id,
            title=str(payload.title or "New chat").strip() or "New chat",
            allocated_tools=allocated.model_dump(),
        )
        chat_payload = self.store.read_chat_file(user_id, companion.id, str(session["session_id"])) or {}
        return CompanionChatSessionRead(
            session_id=str(session["session_id"]),
            companion_id=companion.id,
            ontology_id=int(session["ontology_id"]),
            title=str(session["title"] or "New chat"),
            message_count=len(chat_payload.get("messages") or []),
            created_at=session["created_at"],
            updated_at=session["updated_at"],
            last_message_at=session.get("last_message_at"),
        )

    def get_chat_session(self, *, user_id: int, session_id: str) -> CompanionChatSessionRead:
        companion = self.get_companion(user_id)
        session = self.store.get_session(user_id, session_id)
        if session is None:
            raise KeyError("Companion orchestrator session not found")
        if str(session.get("companion_id")) != str(companion.id):
            raise PermissionError("Companion does not own this orchestrator session")
        chat_payload = self.store.read_chat_file(user_id, companion.id, session_id) or {}
        return CompanionChatSessionRead(
            session_id=session_id,
            companion_id=companion.id,
            ontology_id=int(session["ontology_id"]),
            title=str(session.get("title") or "New chat"),
            message_count=len(chat_payload.get("messages") or []),
            created_at=session["created_at"],
            updated_at=session["updated_at"],
            last_message_at=session.get("last_message_at"),
        )

    def update_chat_session(self, *, user_id: int, session_id: str, payload: CompanionChatSessionUpdateRequest) -> CompanionChatSessionRead:
        current = self.get_chat_session(user_id=user_id, session_id=session_id)
        session = self.store.update_session_title(user_id, session_id, title=payload.title)
        if session is None:
            raise KeyError("Companion orchestrator session not found")
        chat_payload = self.store.read_chat_file(user_id, current.companion_id, session_id) or {}
        return CompanionChatSessionRead(
            session_id=session_id,
            companion_id=current.companion_id,
            ontology_id=current.ontology_id,
            title=str(session.get("title") or payload.title),
            message_count=len(chat_payload.get("messages") or []),
            created_at=session["created_at"],
            updated_at=session["updated_at"],
            last_message_at=session.get("last_message_at"),
        )

    def delete_chat_session(self, *, user_id: int, session_id: str) -> None:
        session = self.store.get_session(user_id, session_id)
        if session is None:
            raise KeyError("Companion orchestrator session not found")
        with self.store.connect() as conn:
            running_jobs = conn.execute(
                "SELECT COUNT(*) AS total FROM turn_jobs WHERE user_id = ? AND session_id = ? AND status IN ('queued','running')",
                (user_id, session_id),
            ).fetchone()
        if int(running_jobs["total"] if running_jobs else 0) > 0:
            raise ValueError("Cannot delete a chat session with active turn jobs")
        self.store.delete_session(user_id, session_id)

    def chat_session_counts(self, *, user_id: int) -> list[CompanionChatSessionCount]:
        return [CompanionChatSessionCount(**item) for item in self.store.count_sessions_by_ontology(user_id)]

    async def queue_turn(
        self,
        *,
        user_id: int,
        session_id: str,
        query: str,
        auth_header: str | None = None,
    ) -> CompanionOrchestratorTurnQueuedResponse:
        logger.info("companion turn queue requested user_id=%s session_id=%s", user_id, session_id)
        companion = self.get_companion(user_id)
        session = self.store.get_session(user_id, session_id)
        if session is None:
            raise KeyError("Companion orchestrator session not found")
        if str(session.get("companion_id")) != str(companion.id):
            raise PermissionError("Companion does not own this orchestrator session")

        ontology_id = int(session.get("ontology_id") or 0)
        logger.info("companion turn refreshing tool allocation user_id=%s session_id=%s ontology_id=%s", user_id, session_id, ontology_id)
        allocated = await self.provider_client.allocate_tools(
            user_id=user_id,
            ontology_id=ontology_id,
            auth_header=auth_header,
        )
        session = self.store.update_session_allocated_tools(user_id, session_id, allocated.model_dump()) or session
        chat_payload = self.store.read_chat_file(user_id, companion.id, session_id) or {}
        conversation_context = self.orchestrator.build_conversation_context(query, chat_payload)
        job_payload = {
            "status": "queued",
            "query": query,
            "session_id": session_id,
            "ontology_id": ontology_id,
            "companion_id": companion.id,
            "allocated_tools": session.get("allocated_tools") or {},
            "conversation_context": conversation_context,
        }
        job_id = self.store.create_turn_job(
            user_id=user_id,
            session_id=session_id,
            ontology_id=ontology_id,
            companion_id=companion.id,
            query=query,
            payload=job_payload,
        )
        self.store.append_chat_message(
            user_id=user_id,
            companion_id=companion.id,
            session_id=session_id,
            role="user",
            content=query,
            metadata={"job_id": job_id, "status": "queued"},
        )
        logger.info(
            "companion turn queued job_id=%s user_id=%s session_id=%s ontology_id=%s elder=%s librarian=%s",
            job_id,
            user_id,
            session_id,
            ontology_id,
            len(allocated.elder),
            len(allocated.librarian),
        )
        task = asyncio.create_task(
            self._run_turn(
                job_id=job_id,
                user_id=user_id,
                session_id=session_id,
                query=query,
                auth_header=auth_header,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return CompanionOrchestratorTurnQueuedResponse(
            job_id=job_id,
            status="queued",
            session_id=session_id,
            ontology_id=ontology_id,
        )

    def _build_running_payload(
        self,
        base_payload: dict[str, Any],
        *,
        phase: str,
        phase_label: str,
        progress_current: int,
        progress_total: int,
        **extra: Any,
    ) -> dict[str, Any]:
        payload = {
            **base_payload,
            "status": "running",
            "phase": phase,
            "phase_label": phase_label,
            "progress": {
                "current": progress_current,
                "total": progress_total,
            },
        }
        payload.update(extra)
        return payload

    async def _run_turn(
        self,
        *,
        job_id: int,
        user_id: int,
        session_id: str,
        query: str,
        auth_header: str | None = None,
    ) -> None:
        logger.info("companion turn started job_id=%s user_id=%s session_id=%s", job_id, user_id, session_id)
        job = self.store.get_turn_job(user_id, job_id)
        payload: dict[str, Any] = dict(job.get("payload") or {}) if job else {}
        try:
            companion = self.get_companion(user_id)
            session = self.store.get_session(user_id, session_id)
            if session is None:
                raise KeyError("Companion orchestrator session not found")
            ontology_id = int(session.get("ontology_id") or 0)
            allocated = session.get("allocated_tools") or {}
            chat_payload = self.store.read_chat_file(user_id, companion.id, session_id) or {}
            conversation_context = self.orchestrator.build_conversation_context(query, chat_payload)
            rapport_profile = self.store.get_or_create_rapport_profile(user_id=user_id, companion_id=companion.id)
            chat_state = self.store.get_or_create_chat_state(user_id=user_id, companion_id=companion.id, session_id=session_id)
            llm_trace: dict[str, Any] = {}
            total_phases = len(self.TURN_PHASES)

            logger.info("companion turn step policy started job_id=%s", job_id)

            payload = self._build_running_payload(
                payload,
                phase="policy",
                phase_label="Planning companion policy",
                progress_current=1,
                progress_total=total_phases,
                conversation_context=conversation_context,
                chat_state=chat_state,
                rapport_profile=rapport_profile,
                llm_trace=llm_trace,
            )
            self.store.update_turn_job(job_id, status="running", payload=payload)
            companion_policy = await self.orchestrator.plan_companion_policy(
                query=query,
                conversation_context=conversation_context,
                chat_state=chat_state,
                rapport_profile=rapport_profile,
                debug_trace=llm_trace,
            )
            logger.info(
                "companion turn step policy happened job_id=%s needs_knowledge_tools=%s",
                job_id,
                companion_policy.get("needs_knowledge_tools"),
            )
            logger.info("companion turn step policy ended job_id=%s", job_id)

            logger.info("companion turn step planning started job_id=%s", job_id)
            payload = self._build_running_payload(
                payload,
                phase="planning",
                phase_label="Planning tool usage",
                progress_current=2,
                progress_total=total_phases,
                conversation_context=conversation_context,
                companion_policy=companion_policy,
                chat_state=chat_state,
                rapport_profile=rapport_profile,
                llm_trace=llm_trace,
            )
            self.store.update_turn_job(job_id, status="running", payload=payload)
            logger.info("companion turn planning query job_id=%s", job_id)
            logger.info(
                "companion turn conversation context job_id=%s resolved_subject=%s rewritten_query=%s recent_messages=%s",
                job_id,
                conversation_context.get("resolved_subject"),
                self._debug_text(conversation_context.get("rewritten_query"), limit=1000),
                len(conversation_context.get("recent_messages_used") or []),
            )
            planning_query = str(conversation_context.get("rewritten_query") or query)
            plan = await self.orchestrator.plan_query(
                planning_query,
                conversation_context=conversation_context,
                allocated_tools=allocated,
                companion_policy=companion_policy,
                debug_trace=llm_trace,
            )
            selected_tools = self.orchestrator.plan_selected_tools(plan, allocated)
            logger.info(
                "companion turn step planning happened job_id=%s strategy=%s steps=%s",
                job_id,
                plan.get("strategy"),
                len(plan.get("steps") or []),
            )
            logger.info("companion turn step planning ended job_id=%s", job_id)
            logger.info("companion turn step selecting_tools started job_id=%s", job_id)
            routing = {
                "use_elder": bool(selected_tools.get("elder")),
                "use_librarian": bool(selected_tools.get("librarian")),
                "reason": str(plan.get("reason") or "planned_execution"),
            }
            payload = self._build_running_payload(
                payload,
                phase="selecting_tools",
                phase_label="Selecting tools",
                progress_current=3,
                progress_total=total_phases,
                routing=routing,
                selected_tools=selected_tools,
                plan=plan,
                conversation_context=conversation_context,
                companion_policy=companion_policy,
                chat_state=chat_state,
                rapport_profile=rapport_profile,
                llm_trace=llm_trace,
            )
            self.store.update_turn_job(job_id, status="running", payload=payload)
            logger.info(
                "companion turn planning completed job_id=%s use_elder=%s use_librarian=%s selected_elder=%s selected_librarian=%s",
                job_id,
                routing.get("use_elder"),
                routing.get("use_librarian"),
                len(selected_tools.get("elder") or []),
                len(selected_tools.get("librarian") or []),
            )
            logger.info("companion turn step selecting_tools ended job_id=%s", job_id)

            async def elder_runner(agent_id: str, tool_query: str) -> dict[str, Any]:
                logger.info("companion turn querying elder job_id=%s agent_id=%s", job_id, agent_id)
                raw = await self.provider_client.run_elder(
                    user_id=user_id,
                    agent_id=agent_id,
                    query=tool_query,
                    ontology_id=ontology_id,
                    auth_header=auth_header,
                )
                return self._normalize_provider_response(raw, agent_id=agent_id, job="elder", allocated=allocated, ontology_id=ontology_id)

            async def librarian_runner(agent_id: str, tool_query: str) -> dict[str, Any]:
                logger.info("companion turn querying librarian job_id=%s agent_id=%s", job_id, agent_id)
                raw = await self.provider_client.run_librarian(
                    user_id=user_id,
                    agent_id=agent_id,
                    query=tool_query,
                    ontology_id=ontology_id,
                    auth_header=auth_header,
                )
                return self._normalize_provider_response(raw, agent_id=agent_id, job="librarian", allocated=allocated, ontology_id=ontology_id)

            total_selected_tools = len(plan.get("steps") or [])
            payload = self._build_running_payload(
                payload,
                phase="executing_steps",
                phase_label="Executing tool plan",
                progress_current=4,
                progress_total=total_phases,
                step_progress={
                    "total": total_selected_tools,
                    "completed": 0,
                    "running": 1 if total_selected_tools else 0,
                },
                execution={"completed_steps": [], "stopped_reason": None},
                companion_policy=companion_policy,
                chat_state=chat_state,
                rapport_profile=rapport_profile,
                llm_trace=llm_trace,
            )
            self.store.update_turn_job(job_id, status="running", payload=payload)
            logger.info("companion turn step executing_steps started job_id=%s", job_id)
            logger.info("companion turn executing plan job_id=%s steps=%s", job_id, total_selected_tools)
            agent_responses: list[dict[str, Any]] = []
            completed_steps: list[dict[str, Any]] = []
            results_by_step: dict[str, dict[str, Any]] = {}
            stopped_reason: str | None = None

            def _latest_elder_result() -> dict[str, Any] | None:
                for response in reversed(agent_responses):
                    if str(response.get("agent_job") or "") == "elder":
                        return response
                return None

            for index, step in enumerate(plan.get("steps") or [], start=1):
                step_id = str(step.get("step_id") or f"step-{index}")
                tool_job = str(step.get("tool_job") or "")
                logger.info(
                    "companion turn step starting job_id=%s step_id=%s index=%s tool_job=%s goal=%s depends_on=%s use_prior_context=%s",
                    job_id,
                    step_id,
                    index,
                    tool_job,
                    self._debug_text(step.get("goal"), limit=1000),
                    json.dumps(step.get("depends_on") or [], ensure_ascii=True),
                    bool(step.get("use_prior_context")),
                )
                selected_agent_ids = selected_tools.get(tool_job) or []
                if not selected_agent_ids:
                    stopped_reason = f"No allocated {tool_job} tool available for {step_id}."
                    logger.warning(
                        "companion turn step stopped job_id=%s step_id=%s reason=%s",
                        job_id,
                        step_id,
                        stopped_reason,
                    )
                    break
                query_used = self.orchestrator.rewrite_query_with_subject(str(step.get("query") or planning_query), conversation_context)
                canon_context = None
                if step.get("use_prior_context"):
                    dependency_ids = [str(item) for item in (step.get("depends_on") or []) if str(item).strip()]
                    dependency_id = ""
                    dependency_result = None
                    for candidate in dependency_ids:
                        maybe = results_by_step.get(candidate)
                        if maybe is not None:
                            dependency_id = candidate
                            dependency_result = maybe
                            break

                    if dependency_result is None and tool_job == "librarian":
                        dependency_result = _latest_elder_result()
                        dependency_id = str((dependency_result or {}).get("step_id") or "")

                    if dependency_result is None:
                        if tool_job == "librarian":
                            logger.info(
                                "companion turn step optional context missing job_id=%s step_id=%s tool_job=%s",
                                job_id,
                                step_id,
                                tool_job,
                            )
                        else:
                            stopped_reason = f"Required prior step {dependency_id or 'unknown'} was unavailable for {step_id}."
                            logger.warning(
                                "companion turn step stopped job_id=%s step_id=%s reason=%s",
                                job_id,
                                step_id,
                                stopped_reason,
                            )
                            break
                    else:
                        canon_context = self.orchestrator.build_canon_context(dependency_result)
                        logger.info(
                            "companion turn step derived context job_id=%s step_id=%s dependency_id=%s context=%s",
                            job_id,
                            step_id,
                            dependency_id or "latest_elder",
                            self._debug_text(json.dumps(canon_context, ensure_ascii=True), limit=3000),
                        )
                        if tool_job == "librarian":
                            if self.orchestrator.canon_context_is_sufficient(canon_context):
                                query_used = self.orchestrator.build_librarian_query(subquery=query_used, canon_context=canon_context)
                            else:
                                logger.info(
                                    "companion turn step optional context insufficient job_id=%s step_id=%s tool_job=%s",
                                    job_id,
                                    step_id,
                                    tool_job,
                                )
                                canon_context = None
                        elif not self.orchestrator.canon_context_is_sufficient(canon_context):
                            stopped_reason = f"Stopped before {step_id}: insufficient grounded canon evidence from {dependency_id or 'unknown'}."
                            logger.warning(
                                "companion turn step stopped job_id=%s step_id=%s reason=%s",
                                job_id,
                                step_id,
                                stopped_reason,
                            )
                            break
                logger.info(
                    "companion turn step request job_id=%s step_id=%s tool_job=%s agent_id=%s query=%s",
                    job_id,
                    step_id,
                    tool_job,
                    (selected_agent_ids[0] if selected_agent_ids else ""),
                    self._debug_text(query_used),
                )
                payload = self._build_running_payload(
                    payload,
                    phase="executing_steps",
                    phase_label="Executing tool plan",
                    progress_current=4,
                    progress_total=total_phases,
                    routing=routing,
                    selected_tools=selected_tools,
                    plan=plan,
                    conversation_context=conversation_context,
                    step_progress={
                        "total": total_selected_tools,
                        "completed": index - 1,
                        "running": 1,
                        "current": index,
                    },
                    current_step={
                        "step_id": step_id,
                        "tool_job": tool_job,
                        "goal": step.get("goal"),
                    },
                    execution={"completed_steps": completed_steps, "stopped_reason": stopped_reason},
                    companion_policy=companion_policy,
                    chat_state=chat_state,
                    rapport_profile=rapport_profile,
                    llm_trace=llm_trace,
                )
                self.store.update_turn_job(job_id, status="running", payload=payload)
                agent_id = selected_agent_ids[0]
                response = await (elder_runner(agent_id, query_used) if tool_job == "elder" else librarian_runner(agent_id, query_used))
                response["step_id"] = step_id
                response["query_used"] = query_used
                if canon_context is not None:
                    response["derived_context"] = canon_context
                logger.info(
                    "companion turn step response job_id=%s step_id=%s tool_job=%s ok=%s agent_name=%s answer=%s error=%s",
                    job_id,
                    step_id,
                    tool_job,
                    response.get("ok", True),
                    response.get("agent_name"),
                    self._debug_text(response.get("answer")),
                    self._debug_text(response.get("error"), limit=1000),
                )
                logger.info("companion turn step %s: this happened ok=%s", step_id, response.get("ok", True))
                agent_responses.append(response)
                results_by_step[step_id] = response
                completed_steps.append(
                    {
                        "step_id": step_id,
                        "tool_job": tool_job,
                        "goal": step.get("goal"),
                        "query_used": query_used,
                        "agent_id": response.get("agent_id"),
                        "agent_name": response.get("agent_name"),
                        "ok": response.get("ok", True),
                        "answer": response.get("answer"),
                    }
                )
                if response.get("ok") is False and str(step.get("on_failure") or "stop") == "stop":
                    stopped_reason = f"Stopped after {step_id}: provider failure."
                    logger.warning(
                        "companion turn step stopped job_id=%s step_id=%s reason=%s",
                        job_id,
                        step_id,
                        stopped_reason,
                    )
                    break
                logger.info("companion turn step ended job_id=%s step_id=%s", job_id, step_id)
            logger.info("companion turn step executing_steps ended job_id=%s", job_id)
            payload = self._build_running_payload(
                payload,
                phase="synthesizing",
                phase_label="Synthesizing answer",
                progress_current=5,
                progress_total=total_phases,
                agent_responses=agent_responses,
                plan=plan,
                execution={"completed_steps": completed_steps, "stopped_reason": stopped_reason},
                step_progress={
                    "total": total_selected_tools,
                    "completed": len(completed_steps),
                    "running": 0,
                },
                current_step=None,
                conversation_context=conversation_context,
                companion_policy=companion_policy,
                chat_state=chat_state,
                rapport_profile=rapport_profile,
                llm_trace=llm_trace,
            )
            self.store.update_turn_job(job_id, status="running", payload=payload)
            logger.info("companion turn synthesizing final response job_id=%s", job_id)
            logger.info("companion turn step synthesizing started job_id=%s", job_id)
            final_text = await self.orchestrator.synthesize_final_answer(
                query=query,
                companion_name=companion.name,
                companion_writing_style=companion.writing_style,
                plan=plan,
                execution={"completed_steps": completed_steps, "stopped_reason": stopped_reason},
                agent_responses=agent_responses,
                debug_trace=llm_trace,
            )
            logger.info("companion turn step synthesizing happened job_id=%s chars=%s", job_id, len(final_text))
            logger.info("companion turn step synthesizing ended job_id=%s", job_id)
            logger.info("companion turn step reflection started job_id=%s", job_id)
            payload = self._build_running_payload(
                payload,
                phase="reflection",
                phase_label="Evaluating response quality",
                progress_current=6,
                progress_total=total_phases,
                plan=plan,
                execution={"completed_steps": completed_steps, "stopped_reason": stopped_reason},
                selected_tools=selected_tools,
                conversation_context=conversation_context,
                companion_policy=companion_policy,
                chat_state=chat_state,
                rapport_profile=rapport_profile,
                final={"text": final_text},
                llm_trace=llm_trace,
            )
            self.store.update_turn_job(job_id, status="running", payload=payload)

            reflection = await self.orchestrator.evaluate_turn_reflection(
                query=query,
                final_text=final_text,
                execution={"completed_steps": completed_steps, "stopped_reason": stopped_reason},
                chat_state=chat_state,
                rapport_profile=rapport_profile,
                debug_trace=llm_trace,
            )
            proactivity = reflection.get("proactivity") if isinstance(reflection.get("proactivity"), dict) else {}
            proactive_message = str(proactivity.get("proactive_message") or "").strip()
            if proactivity.get("should_be_proactive") and proactive_message and int(self.settings.max_proactive_nudges_per_turn) > 0:
                final_text = f"{final_text}\n\n{proactive_message}"

            updated_chat_state = self.store.apply_chat_state_patch(
                user_id=user_id,
                companion_id=companion.id,
                session_id=session_id,
                patch=reflection.get("chat_state_patch") if isinstance(reflection.get("chat_state_patch"), dict) else {},
                fallback_goal=str(companion_policy.get("chat_goal") or "").strip() or None,
                fallback_intention=str(companion_policy.get("turn_intention") or "").strip() or None,
                fallback_mode=str(companion_policy.get("conversation_mode") or "").strip() or None,
                fallback_open_threads=companion_policy.get("open_threads") if isinstance(companion_policy.get("open_threads"), list) else None,
                fallback_next_actions=companion_policy.get("next_best_actions") if isinstance(companion_policy.get("next_best_actions"), list) else None,
                recent_user_state=reflection.get("user_state_estimate") if isinstance(reflection.get("user_state_estimate"), dict) else None,
            )
            updated_rapport = self.store.apply_rapport_patch(
                user_id=user_id,
                companion_id=companion.id,
                patch=reflection.get("rapport_patch") if isinstance(reflection.get("rapport_patch"), list) else [],
                confidence_threshold=float(self.settings.rapport_confidence_threshold),
                max_delta=float(self.settings.rapport_max_trait_delta_per_turn),
                min_value=float(self.settings.rapport_min_trait_value),
                max_value=float(self.settings.rapport_max_trait_value),
                recent_user_state=reflection.get("user_state_estimate") if isinstance(reflection.get("user_state_estimate"), dict) else None,
            )
            self.store.create_turn_reflection(
                user_id=user_id,
                companion_id=companion.id,
                session_id=session_id,
                turn_job_id=job_id,
                reflection=reflection,
            )
            logger.info(
                "companion turn step reflection happened job_id=%s answered_user=%s",
                job_id,
                reflection.get("answered_user"),
            )
            logger.info("companion turn step reflection ended job_id=%s", job_id)
            logger.info("companion turn final response synthesized job_id=%s chars=%s", job_id, len(final_text))
            done_payload = self.orchestrator.build_turn_payload(
                session_id=session_id,
                query=query,
                plan=plan,
                execution={"completed_steps": completed_steps, "stopped_reason": stopped_reason},
                selected_tools=selected_tools,
                agent_responses=agent_responses,
                final_text=final_text,
                conversation_context=conversation_context,
                companion_policy=companion_policy,
                turn_reflection=reflection,
                chat_state=updated_chat_state,
                rapport_profile=updated_rapport,
                rapport_patch_applied=updated_rapport.get("applied_patch") if isinstance(updated_rapport.get("applied_patch"), list) else [],
                llm_trace=llm_trace,
            )
            self.store.update_turn_job(job_id, status="done", payload=done_payload)
            self.store.append_chat_message(
                user_id=user_id,
                companion_id=companion.id,
                session_id=session_id,
                role="assistant",
                content=final_text,
                metadata={"job_id": job_id, "status": "done", "payload": done_payload},
            )
            updated_chat = self.store.read_chat_file(user_id, companion.id, session_id) or {}
            updated_memory = self.orchestrator.update_conversation_memory(
                chat_payload=updated_chat,
                query=query,
                final_text=final_text,
                agent_responses=agent_responses,
            )
            self.store.update_chat_memory(
                user_id=user_id,
                companion_id=companion.id,
                session_id=session_id,
                memory=updated_memory,
            )
            logger.info("companion turn completed job_id=%s", job_id)
        except Exception as exc:
            logger.exception("companion turn failed job_id=%s error=%s", job_id, exc)
            failed_payload = {**payload, "status": "failed", "error": str(exc)}
            self.store.update_turn_job(job_id, status="failed", payload=failed_payload, error=str(exc))

    @staticmethod
    def _normalize_provider_response(
        raw: dict[str, Any], *, agent_id: str, job: str, allocated: dict[str, Any], ontology_id: int
    ) -> dict[str, Any]:
        agent_name = agent_id
        for item in allocated.get(job, []) or []:
            if str(item.get("id")) == str(agent_id):
                agent_name = str(item.get("name") or agent_id)
                break
        sources = raw.get("sources") or raw.get("evidence") or []
        if job == "librarian":
            sources = raw.get("sources_used") or raw.get("chunks") or sources
        if raw.get("ok") is False:
            return {
                "ok": False,
                "agent_id": agent_id,
                "agent_name": raw.get("agent_name") or agent_name,
                "agent_job": job,
                "ontology_id": ontology_id,
                "answer": raw.get("answer") or "",
                "sources": sources,
                "error": raw.get("error") or "provider failed",
            }
        return {
            "ok": True,
            "agent_id": str(raw.get("agent_id") or agent_id),
            "agent_name": str(raw.get("agent_name") or agent_name),
            "agent_job": str(raw.get("agent_job") or job),
            "ontology_id": ontology_id,
            "answer": str(raw.get("answer") or raw.get("text") or raw.get("content") or ""),
            "sources": sources,
        }
