"""Repository for Novelist runs."""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novelist import NovelistRun, NovelistRunStatus, NovelistStage


class NovelistRepository:
    """Persistence helper for NovelistRun."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_run(
        self,
        *,
        agent_id: str,
        ontology_id: int | None,
        ontology_instance_id: str | None,
        settings: dict[str, Any] | None,
        request_payload: dict[str, Any],
    ) -> NovelistRun:
        run = NovelistRun(
            agent_id=agent_id,
            ontology_id=ontology_id,
            ontology_instance_id=ontology_instance_id,
            settings=settings,
            request_payload=request_payload,
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def get_run(self, run_id: str) -> NovelistRun | None:
        stmt = select(NovelistRun).where(NovelistRun.id == run_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_runs(
        self,
        *,
        agent_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[NovelistRun]:
        stmt = select(NovelistRun).order_by(NovelistRun.created_at.desc())
        if agent_id:
            stmt = stmt.where(NovelistRun.agent_id == agent_id)
        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def delete_run(self, run_id: str, *, agent_id: str | None = None) -> int:
        run = await self.get_run(run_id)
        if not run:
            return 0
        if agent_id and run.agent_id != agent_id:
            return 0
        await self.session.delete(run)
        await self.session.flush()
        return 1

    async def attach_background_job(self, run_id: str, job_id: int) -> None:
        run = await self.get_run(run_id)
        if not run:
            return
        run.background_job_id = job_id
        await self.session.flush()

    async def update_status(
        self,
        run_id: str,
        *,
        status: NovelistRunStatus | None = None,
        stage: NovelistStage | None = None,
        chunks: list[dict[str, Any]] | None = None,
        draft_text: str | None = None,
        critic_notes: str | None = None,
        error_message: str | None = None,
    ) -> NovelistRun | None:
        run = await self.get_run(run_id)
        if not run:
            return None
        if status:
            run.status = status
        if stage:
            run.stage = stage
        if chunks is not None:
            run.chunks = chunks
        if draft_text is not None:
            run.draft_text = draft_text
        if critic_notes is not None:
            run.critic_notes = critic_notes
        if error_message is not None:
            run.error_message = error_message
        await self.session.flush()
        return run
