from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException
from app.schemas.architect import ArchitectAnalysisRequest
from app.services.creative_workshop_service import require_workshop_world
from app.services.ontology_instance_service import OntologyInstanceService

from app.models.architect import (
    ArchitectAnalysisRun,
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

    async def create_world_run(
        self,
        ontology_id: int,
        payload: ArchitectAnalysisRequest,
        actor_id: str,
        instances: OntologyInstanceService,
    ) -> ArchitectAnalysisRun:
        await require_workshop_world(self.session, ontology_id)
        if payload.ontology_id is not None and payload.ontology_id != ontology_id:
            raise HTTPException(
                status_code=400, detail="Payload world must match selected world"
            )
        try:
            source = await instances.get_instance(
                payload.ontology_instance_id,
                include_scenes=False,
                include_relationships=False,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Source page not found") from exc
        if source.ontology_id != ontology_id:
            raise HTTPException(
                status_code=400, detail="Source page does not belong to the selected world"
            )
        return await self.create_run(
            agent_id=None,
            ontology_id=ontology_id,
            ontology_instance_id=payload.ontology_instance_id,
            settings={
                "requested_by": actor_id,
                "max_chunks": payload.max_chunks,
                "chunk_size": payload.chunk_size,
            },
        )

    async def get_run(self, run_id: str, *, include_proposals: bool = True):
        run = await self.repository.get_run(run_id, with_proposals=include_proposals)
        return run

    async def list_runs_for_agent(
        self, agent_id: str, *, limit: int = 20, offset: int = 0
    ):
        return await self.repository.list_runs_for_agent(
            agent_id, limit=limit, offset=offset
        )

    async def list_runs_for_world(
        self, ontology_id: int, *, limit: int = 20, offset: int = 0
    ) -> Sequence[ArchitectAnalysisRun]:
        return await self.repository.list_runs_for_world(
            ontology_id, limit=limit, offset=offset
        )

    async def delete_runs_for_world(self, ontology_id: int) -> int:
        deleted = await self.repository.delete_runs_for_world(ontology_id)
        await self.session.commit()
        return deleted

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

    async def delete_run(
        self,
        run_id: str,
        *,
        agent_id: str | None = None,
    ) -> int:
        deleted = await self.repository.delete_run(run_id, agent_id=agent_id)
        await self.session.commit()
        return deleted

    async def delete_runs_for_agent(self, agent_id: str) -> int:
        deleted = await self.repository.delete_runs_for_agent(agent_id)
        await self.session.commit()
        return deleted

    async def proposal_counts(self, run_id: str) -> dict[str, int]:
        return await self.repository.proposal_counts(run_id)
