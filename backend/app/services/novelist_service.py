"""Service layer for Novelist runs."""

from __future__ import annotations

from typing import Any, Sequence

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novelist import NovelistRun, NovelistRunStatus, NovelistStage
from app.repositories.novelist_repository import NovelistRepository


class NovelistService:
    """Business logic wrapper for Novelist runs."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = NovelistRepository(session)

    async def create_run(
        self,
        *,
        agent_id: str,
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
        self, *, agent_id: str | None = None, limit: int = 50, offset: int = 0
    ) -> Sequence[NovelistRun]:
        return await self.repo.list_runs(agent_id=agent_id, limit=limit, offset=offset)

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
        chunks: list[dict[str, Any]] | None = None,
        draft_text: str | None = None,
        critic_notes: str | None = None,
        error_message: str | None = None,
    ) -> NovelistRun:
        run = await self.repo.update_status(
            run_id,
            status=status,
            stage=stage,
            chunks=chunks,
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
