"""Service layer for Agent business logic."""

from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.agent_repository import AgentRepository
from app.schemas.agent import AgentCreate, AgentRead, AgentUpdate


class AgentService:
    """Service for managing agents."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = AgentRepository(session)

    async def create_agent(self, agent_data: AgentCreate) -> AgentRead:
        """Create a new agent."""
        # Validate job type
        valid_jobs = ["elder", "librarian", "architect"]
        if agent_data.job not in valid_jobs:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid job type. Must be one of: {', '.join(valid_jobs)}",
            )

        agent = await self.repository.create(agent_data)
        await self.session.commit()

        return AgentRead(
            id=agent.id,
            name=agent.name,
            avatar_url=agent.avatar_url,
            description=agent.description,
            writing_style=agent.writing_style,
            job=agent.job,
            active=agent.active,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
            ontology_ids=[ont.id for ont in agent.ontologies],
        )

    async def get_agent(self, agent_id: str) -> AgentRead:
        """Get an agent by ID."""
        agent = await self.repository.get_by_id(agent_id)
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent not found",
            )

        return AgentRead(
            id=agent.id,
            name=agent.name,
            avatar_url=agent.avatar_url,
            description=agent.description,
            writing_style=agent.writing_style,
            job=agent.job,
            active=agent.active,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
            ontology_ids=[ont.id for ont in agent.ontologies],
        )

    async def list_agents(
        self,
        job: Optional[str] = None,
        active: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgentRead]:
        """List agents with optional filters."""
        agents = await self.repository.list(
            job=job, active=active, limit=limit, offset=offset
        )

        return [
            AgentRead(
                id=agent.id,
                name=agent.name,
                avatar_url=agent.avatar_url,
                description=agent.description,
                writing_style=agent.writing_style,
                job=agent.job,
                active=agent.active,
                created_at=agent.created_at,
                updated_at=agent.updated_at,
                ontology_ids=[ont.id for ont in agent.ontologies],
            )
            for agent in agents
        ]

    async def update_agent(self, agent_id: str, agent_data: AgentUpdate) -> AgentRead:
        """Update an agent."""
        # Validate job type if provided
        if agent_data.job is not None:
            valid_jobs = ["elder", "librarian", "architect"]
            if agent_data.job not in valid_jobs:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid job type. Must be one of: {', '.join(valid_jobs)}",
                )

        agent = await self.repository.update(agent_id, agent_data)
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent not found",
            )
        # Commit and re-fetch eagerly to avoid async lazy-load after commit
        await self.session.commit()

        refreshed = await self.repository.get_by_id(agent.id)
        if not refreshed:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent not found after update",
            )

        return AgentRead(
            id=refreshed.id,
            name=refreshed.name,
            avatar_url=refreshed.avatar_url,
            description=refreshed.description,
            writing_style=refreshed.writing_style,
            job=refreshed.job,
            active=refreshed.active,
            created_at=refreshed.created_at,
            updated_at=refreshed.updated_at,
            ontology_ids=[ont.id for ont in refreshed.ontologies],
        )

    async def delete_agent(self, agent_id: str) -> None:
        """Delete an agent."""
        success = await self.repository.delete(agent_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent not found",
            )

        await self.session.commit()

    async def attach_ontology(self, agent_id: str, ontology_id: int) -> AgentRead:
        """Attach an ontology to an agent."""
        agent = await self.repository.attach_ontology(agent_id, ontology_id)
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent or ontology not found",
            )

        await self.session.commit()

        refreshed = await self.repository.get_by_id(agent.id)
        if not refreshed:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent not found after attach",
            )

        return AgentRead(
            id=refreshed.id,
            name=refreshed.name,
            avatar_url=refreshed.avatar_url,
            description=refreshed.description,
            writing_style=refreshed.writing_style,
            job=refreshed.job,
            active=refreshed.active,
            created_at=refreshed.created_at,
            updated_at=refreshed.updated_at,
            ontology_ids=[ont.id for ont in refreshed.ontologies],
        )

    async def detach_ontology(self, agent_id: str, ontology_id: int) -> AgentRead:
        """Detach an ontology from an agent."""
        agent = await self.repository.detach_ontology(agent_id, ontology_id)
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent not found",
            )

        await self.session.commit()

        refreshed = await self.repository.get_by_id(agent.id)
        if not refreshed:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent not found after detach",
            )

        return AgentRead(
            id=refreshed.id,
            name=refreshed.name,
            avatar_url=refreshed.avatar_url,
            description=refreshed.description,
            writing_style=refreshed.writing_style,
            job=refreshed.job,
            active=refreshed.active,
            created_at=refreshed.created_at,
            updated_at=refreshed.updated_at,
            ontology_ids=[ont.id for ont in refreshed.ontologies],
        )

    @staticmethod
    def get_available_jobs() -> list[str]:
        """Get list of available job types."""
        return ["elder", "librarian", "architect"]
