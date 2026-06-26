"""Repository for Agent model."""

from __future__ import annotations

from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.agent import Agent
from app.models.ontology import Ontology
from app.schemas.agent import AgentCreate, AgentUpdate


class AgentRepository:
    """Repository for managing Agent persistence."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, agent_data: AgentCreate) -> Agent:
        """Create a new agent."""
        agent = Agent(
            id=str(uuid4()),
            name=agent_data.name,
            avatar_url=agent_data.avatar_url,
            description=agent_data.description,
            writing_style=agent_data.writing_style,
            job=agent_data.job,
            active=agent_data.active,
        )

        # Link ontologies
        if agent_data.ontology_ids:
            stmt = select(Ontology).where(Ontology.id.in_(agent_data.ontology_ids))
            result = await self.session.execute(stmt)
            ontologies = result.scalars().all()
            agent.ontologies = list(ontologies)

        self.session.add(agent)
        await self.session.flush()
        await self.session.refresh(agent, ["ontologies"])
        return agent

    async def get_by_id(self, agent_id: str) -> Optional[Agent]:
        """Get an agent by ID."""
        stmt = (
            select(Agent)
            .where(Agent.id == agent_id)
            .options(selectinload(Agent.ontologies))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        job: Optional[str] = None,
        active: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Agent]:
        """List agents with optional filters."""
        stmt = select(Agent).options(selectinload(Agent.ontologies))

        if job is not None:
            stmt = stmt.where(Agent.job == job)
        if active is not None:
            stmt = stmt.where(Agent.active == active)

        stmt = stmt.limit(limit).offset(offset).order_by(Agent.created_at.desc())

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update(self, agent_id: str, agent_data: AgentUpdate) -> Optional[Agent]:
        """Update an agent."""
        # Fetch with eager loading to avoid detached instance issues
        stmt = (
            select(Agent)
            .where(Agent.id == agent_id)
            .options(selectinload(Agent.ontologies))
        )
        result = await self.session.execute(stmt)
        agent = result.scalar_one_or_none()

        if not agent:
            return None

        # Update fields if provided
        update_data = agent_data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(agent, field, value)

        await self.session.flush()
        return agent

    async def delete(self, agent_id: str) -> bool:
        """Delete an agent."""
        agent = await self.get_by_id(agent_id)
        if not agent:
            return False

        await self.session.delete(agent)
        await self.session.flush()
        return True

    async def attach_ontology(self, agent_id: str, ontology_id: int) -> Optional[Agent]:
        """Attach an ontology to an agent."""
        agent = await self.get_by_id(agent_id)
        if not agent:
            return None

        # Check if ontology exists
        stmt = select(Ontology).where(Ontology.id == ontology_id)
        result = await self.session.execute(stmt)
        ontology = result.scalar_one_or_none()

        if not ontology:
            return None

        # Add ontology if not already linked
        if ontology not in agent.ontologies:
            agent.ontologies.append(ontology)
            await self.session.flush()
            await self.session.refresh(agent, ["ontologies"])

        return agent

    async def detach_ontology(self, agent_id: str, ontology_id: int) -> Optional[Agent]:
        """Detach an ontology from an agent."""
        agent = await self.get_by_id(agent_id)
        if not agent:
            return None

        # Remove ontology if linked
        agent.ontologies = [ont for ont in agent.ontologies if ont.id != ontology_id]
        await self.session.flush()
        await self.session.refresh(agent, ["ontologies"])

        return agent

    async def list_active_by_ontology_and_jobs(
        self,
        *,
        ontology_id: int,
        jobs: list[str],
    ) -> list[Agent]:
        """List active agents linked to an ontology and constrained by job types."""
        if not jobs:
            return []

        stmt = (
            select(Agent)
            .join(Agent.ontologies)
            .where(
                Ontology.id == ontology_id,
                Agent.active.is_(True),
                Agent.job.in_(jobs),
            )
            .options(selectinload(Agent.ontologies))
            .order_by(Agent.name.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().unique().all())
