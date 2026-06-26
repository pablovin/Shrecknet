"""Service helpers for Companion Herald Orchestrator bootstrap and queuing."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ontology import Ontology
from app.repositories.agent_repository import AgentRepository
from app.repositories.ontology_repository import OntologyRepository
from app.schemas.personal_companion_orchestrator import (
    AllocatedToolAgent,
    OrchestratorToolAllocation,
)


class HeraldOrchestratorService:
    """World resolution and tool allocation for companion orchestrator."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.agent_repository = AgentRepository(session)
        self.ontology_repository = OntologyRepository(session)

    async def resolve_ontology_or_404(self, ontology_id: int) -> Ontology:
        ontology = await self.ontology_repository.get(ontology_id)
        if ontology is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Ontology not found",
            )
        return ontology

    async def allocate_tools(self, ontology_id: int) -> OrchestratorToolAllocation:
        agents = await self.agent_repository.list_active_by_ontology_and_jobs(
            ontology_id=ontology_id,
            jobs=["elder", "librarian"],
        )

        elder: list[AllocatedToolAgent] = []
        librarian: list[AllocatedToolAgent] = []
        for agent in agents:
            payload = AllocatedToolAgent(
                id=agent.id,
                name=agent.name,
                job=agent.job,
                ontology_ids=[int(ont.id) for ont in agent.ontologies],
            )
            if agent.job == "elder":
                elder.append(payload)
            elif agent.job == "librarian":
                librarian.append(payload)

        if not elder and not librarian:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "No active elder or librarian agents are linked to this ontology"
                ),
            )

        return OrchestratorToolAllocation(elder=elder, librarian=librarian)
