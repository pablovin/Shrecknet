from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.architect import (
    ArchitectProposalStatus,
    ArchitectRunStatus,
)
from app.repositories.architect_repository import ArchitectRepository


class ArchitectService:
    """Business logic wrapper around architect repositories."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = ArchitectRepository(session)

    async def create_run(
        self,
        *,
        agent_id: str | None,
        ontology_id: int | None,
        ontology_instance_id: str,
        settings: dict[str, Any] | None = None,
    ):
        run = await self.repository.create_run(
            agent_id=agent_id,
            ontology_id=ontology_id,
            ontology_instance_id=ontology_instance_id,
            background_job_id=None,
            settings=settings,
        )
        await self.session.commit()
        return run

    async def get_run(self, run_id: str, *, include_proposals: bool = True):
        run = await self.repository.get_run(run_id, with_proposals=include_proposals)
        return run

    async def list_runs_for_agent(
        self, agent_id: str, *, limit: int = 20, offset: int = 0
    ):
        return await self.repository.list_runs_for_agent(
            agent_id, limit=limit, offset=offset
        )

    async def attach_background_job(self, run_id: str, job_id: int) -> None:
        await self.repository.attach_background_job(run_id, job_id)
        await self.session.commit()

    async def update_run_status(
        self,
        run_id: str,
        *,
        status: ArchitectRunStatus,
        input_chunk_count: int | None = None,
    ) -> None:
        await self.repository.update_run_status(
            run_id, status=status, input_chunk_count=input_chunk_count
        )
        await self.session.commit()

    async def insert_proposals(
        self,
        run_id: str,
        proposals: Sequence[dict[str, Any]],
    ) -> None:
        await self.repository.add_proposals(run_id, proposals)
        await self.session.commit()

    async def update_proposal_states(
        self,
        proposal_ids: Sequence[str],
        *,
        status: ArchitectProposalStatus,
    ) -> int:
        count = await self.repository.update_proposal_states(
            proposal_ids, status=status
        )
        await self.session.commit()
        return count
