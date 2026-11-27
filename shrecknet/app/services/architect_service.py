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


async def resolve_world_source_destination(
    ontology_id: int,
    source_id: str,
    section_id: str | None,
    instances: OntologyInstanceService,
) -> str:
    """Resolve the graph container, not a legacy narrative EntityInstance.

    Validate the saved world-scoped Markdown before creating any graph state.
    Reuse existing scene ownership or a valid legacy import destination.
    New narratives get an empty, idempotent source-specific graph container;
    the existing analysis and generation pipeline still receives a concrete ID.
    """
    import asyncio
    from hashlib import sha256
    from app.schemas.ontology_instance import OntologyInstanceCreate
    from app.services.architect_source_documents import load_architect_source

    try:
        source, _ = await asyncio.to_thread(load_architect_source, ontology_id, source_id, section_id)
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=503, detail="Narrative source storage is unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    result = await instances.graph_session.run("""
        MATCH (page:OntologyInstance)-[:HAS_SCENE]->(scene:Scene)
        WHERE toInteger(page.ontology_id) = $ontology_id
          AND toInteger(scene.ontology_id) = $ontology_id
          AND scene.source_id = $source_id
        RETURN DISTINCT page.instance_id AS instance_id
        ORDER BY instance_id
    """, ontology_id=ontology_id, source_id=source_id)
    destinations = [str(row["instance_id"]) for row in await result.data() if row["instance_id"]]
    if len(destinations) == 1:
        return destinations[0]
    if len(destinations) > 1:
        raise HTTPException(status_code=409, detail="This narrative already has scenes on multiple graph pages. Select the intended destination under Advanced configuration.")
    if source.legacy_instance_id:
        try:
            page = await instances.get_instance(source.legacy_instance_id, include_scenes=False, include_relationships=False)
        except ValueError:
            page = None
        if page is not None and page.ontology_id == ontology_id:
            return page.instance_id

    # Stable identity is independent of the title, revision and section.
    key = f"architect-source:{ontology_id}:{source_id}"
    page = await instances.create_instance(
        OntologyInstanceCreate(ontology_id=ontology_id, name=source.title, entities=[]),
        creation_key=key,
        creation_fingerprint=sha256(key.encode("utf-8")).hexdigest(),
        trigger_background_jobs=False,
        bounded_result=True,
    )
    if page.ontology_id != ontology_id:
        raise HTTPException(status_code=409, detail="Narrative destination does not belong to this world")
    return page.instance_id


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
                "source_id": payload.source_id,
                "section_id": payload.section_id,
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
