from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.architect import (
    ArchitectAnalysisRun,
    ArchitectProposal,
    ArchitectProposalStatus,
    ArchitectRunStatus,
)


class ArchitectRepository:
    """Persistence layer for architect analysis runs and proposals."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_run(
        self,
        *,
        agent_id: str | None,
        ontology_id: int | None,
        ontology_instance_id: str,
        background_job_id: int | None,
        settings: dict[str, Any] | None = None,
    ) -> ArchitectAnalysisRun:
        run = ArchitectAnalysisRun(
            agent_id=agent_id,
            ontology_id=ontology_id,
            ontology_instance_id=ontology_instance_id,
            background_job_id=background_job_id,
            settings=settings,
            status=ArchitectRunStatus.PENDING,
        )
        self.session.add(run)
        await self.session.flush()
        await self.session.refresh(run)
        return run

    async def get_run(
        self, run_id: str, with_proposals: bool = True
    ) -> ArchitectAnalysisRun | None:
        stmt: Select[ArchitectAnalysisRun] = select(ArchitectAnalysisRun).where(
            ArchitectAnalysisRun.id == run_id
        )
        if with_proposals:
            stmt = stmt.options(selectinload(ArchitectAnalysisRun.proposals))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_runs_for_agent(
        self,
        agent_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[ArchitectAnalysisRun]:
        stmt = (
            select(ArchitectAnalysisRun)
            .where(ArchitectAnalysisRun.agent_id == agent_id)
            .order_by(ArchitectAnalysisRun.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def add_proposals(
        self,
        run_id: str,
        proposals: Sequence[dict[str, Any]],
    ) -> list[ArchitectProposal]:
        db_objects: list[ArchitectProposal] = []
        for payload in proposals:
            db_objects.append(
                ArchitectProposal(
                    run_id=run_id,
                    proposal_type=payload["proposal_type"],
                    status=payload.get("status", ArchitectProposalStatus.PENDING),
                    entity_definition_id=payload.get("entity_definition_id"),
                    entity_instance_id=payload.get("entity_instance_id"),
                    alias=payload.get("alias"),
                    confidence=payload.get("confidence"),
                    justification=payload.get("justification"),
                    evidence=payload.get("evidence"),
                    proposal_metadata=payload.get("proposal_metadata"),
                    chunks=payload.get("chunks"),
                )
            )
        self.session.add_all(db_objects)
        await self.session.flush()
        return db_objects

    async def insert_proposals(
        self,
        run_id: str,
        proposals: Sequence[dict[str, Any]],
    ) -> list[ArchitectProposal]:
        return await self.add_proposals(run_id, proposals)

    async def update_run_status(
        self,
        run_id: str,
        *,
        status: ArchitectRunStatus,
        input_chunk_count: int | None = None,
    ) -> None:
        stmt = (
            update(ArchitectAnalysisRun)
            .where(ArchitectAnalysisRun.id == run_id)
            .values(status=status, input_chunk_count=input_chunk_count)
        )
        await self.session.execute(stmt)

    async def attach_background_job(self, run_id: str, job_id: int) -> None:
        stmt = (
            update(ArchitectAnalysisRun)
            .where(ArchitectAnalysisRun.id == run_id)
            .values(background_job_id=job_id)
        )
        await self.session.execute(stmt)

    async def update_proposal_states(
        self,
        proposal_ids: Sequence[str],
        *,
        status: ArchitectProposalStatus,
    ) -> int:
        stmt = (
            update(ArchitectProposal)
            .where(ArchitectProposal.id.in_(proposal_ids))
            .values(status=status)
        )
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def update_proposal_validation(
        self,
        proposal_id: str,
        *,
        status: ArchitectProposalStatus,
        corrected_alias: str | None = None,
        corrected_entity_definition_id: int | None = None,
        merged_into_proposal_id: str | None = None,
    ) -> None:
        """Update a proposal with validation data from the client."""
        values: dict[str, Any] = {"status": status}
        if corrected_alias is not None:
            values["corrected_alias"] = corrected_alias
        if corrected_entity_definition_id is not None:
            values["corrected_entity_definition_id"] = corrected_entity_definition_id
        if merged_into_proposal_id is not None:
            values["merged_into_proposal_id"] = merged_into_proposal_id

        stmt = update(ArchitectProposal).where(ArchitectProposal.id == proposal_id).values(**values)
        await self.session.execute(stmt)

    async def update_proposal_generated_entity(
        self, proposal_id: str, entity_instance_id: str
    ) -> None:
        """Update a proposal with the generated entity instance ID."""
        stmt = (
            update(ArchitectProposal)
            .where(ArchitectProposal.id == proposal_id)
            .values(generated_entity_instance_id=entity_instance_id)
        )
        await self.session.execute(stmt)

    async def get_proposals_by_ids(
        self, proposal_ids: Sequence[str]
    ) -> list[ArchitectProposal]:
        """Get proposals by their IDs."""
        stmt = select(ArchitectProposal).where(ArchitectProposal.id.in_(proposal_ids))
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_proposals_by_run(self, run_id: str) -> list[ArchitectProposal]:
        """Get all proposals for a run."""
        stmt = select(ArchitectProposal).where(ArchitectProposal.run_id == run_id)
        result = await self.session.execute(stmt)
        return list(result.scalars())
