"""Service layer for Novelist runs."""

from __future__ import annotations

from typing import Any, Sequence

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novelist import NovelistRun, NovelistRunStatus, NovelistStage
from app.repositories.agent_repository import AgentRepository
from app.repositories.novelist_repository import NovelistRepository
from app.schemas.novelist import NovelistRunCreate


class NovelistService:
    """Business logic wrapper for Novelist runs."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = NovelistRepository(session)
        self.agent_repo = AgentRepository(session)

    async def resolve_and_save_preferences(
        self, *, agent_id: str | None, language: str | None, instructions: str | None
    ) -> tuple[str | None, str | None]:
        """Resolve omitted preferences from the agent and save values for this run.

        Agentless runs use per-request preferences and default to English.
        For legacy agents, empty strings explicitly clear a saved value. Null values reuse the
        previous preference; a first run with no preference resolves to null.
        """
        if agent_id is None:
            return (language or "en").strip() or "en", (instructions or "").strip() or None
        agent = await self.agent_repo.get_by_id(agent_id)
        if not agent or agent.job != "novelist":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Novelist agent not found",
            )

        resolved_language = (
            agent.novelist_last_language
            if language is None
            else language.strip() or None
        )
        resolved_instructions = (
            agent.novelist_last_instructions
            if instructions is None
            else instructions.strip() or None
        )
        await self.agent_repo.update_novelist_preferences(
            agent,
            language=resolved_language,
            instructions=resolved_instructions,
        )
        return resolved_language, resolved_instructions

    async def prepare_world_request(
        self, ontology_id: int, payload: NovelistRunCreate
    ) -> NovelistRunCreate:
        if payload.previous_novelist_run_id:
            previous = await self.get_run(payload.previous_novelist_run_id)
            if previous.ontology_id != ontology_id:
                raise HTTPException(
                    status_code=400, detail="Previous run belongs to a different world"
                )
        # Internal continuity text must be resolved by the worker in the run's world.
        return payload.model_copy(
            update={"previous_session_text": None, "previous_session_summary": None}
        )

    async def create_run(
        self,
        *,
        agent_id: str | None,
        ontology_id: int | None,
        ontology_instance_id: str | None,
        settings: dict[str, Any] | None,
        request_payload: dict[str, Any],
    ) -> NovelistRun:
        run = await self.repo.create_run(
            agent_id=agent_id,
            ontology_id=ontology_id,
            ontology_instance_id=ontology_instance_id,
            settings=settings,
            request_payload=request_payload,
        )
        await self.session.commit()
        return run

    async def get_run(self, run_id: str) -> NovelistRun:
        run = await self.repo.get_run(run_id)
        if not run:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Novelist run not found"
            )
        return run

    async def list_runs(
        self, *, agent_id: str | None = None, ontology_id: int | None = None, limit: int = 50, offset: int = 0
    ) -> Sequence[NovelistRun]:
        return await self.repo.list_runs(agent_id=agent_id, ontology_id=ontology_id, limit=limit, offset=offset)

    async def delete_run(self, run_id: str, *, agent_id: str | None = None) -> int:
        deleted = await self.repo.delete_run(run_id, agent_id=agent_id)
        await self.session.commit()
        return deleted

    async def attach_job(self, run_id: str, job_id: int) -> None:
        await self.repo.attach_background_job(run_id, job_id)
        await self.session.commit()

    async def mark_status(
        self,
        run_id: str,
        *,
        status: NovelistRunStatus | None = None,
        stage: NovelistStage | None = None,
        artifacts: dict[str, Any] | None = None,
        draft_text: str | None = None,
        critic_notes: str | None = None,
        error_message: str | None = None,
    ) -> NovelistRun:
        run = await self.repo.update_status(
            run_id,
            status=status,
            stage=stage,
            artifacts=artifacts,
            draft_text=draft_text,
            critic_notes=critic_notes,
            error_message=error_message,
        )
        if not run:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Novelist run not found"
            )
        await self.session.commit()
        return run
