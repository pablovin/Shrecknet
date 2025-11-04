"""API router for Agent management."""

from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin_user, get_current_user, get_db_session
from app.models.user import User
from app.schemas.agent import AgentCreate, AgentRead, AgentUpdate
from app.services.agent_service import AgentService

router = APIRouter(prefix="/agents", tags=["agents"])


async def get_agent_service(
    session: AsyncSession = Depends(get_db_session),
) -> AgentService:
    """Dependency to get AgentService."""
    return AgentService(session)


@router.get("/jobs", response_model=list[str])
async def get_available_jobs(
    _current_user: User = Depends(get_current_admin_user),
) -> list[str]:
    """
    Get list of available agent job types.

    Requires admin role.
    """
    return AgentService.get_available_jobs()


@router.get("/", response_model=list[AgentRead])
async def list_agents(
    job: Optional[str] = Query(None, description="Filter by job type"),
    active: Optional[bool] = Query(None, description="Filter by active status"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),    
    service: AgentService = Depends(get_agent_service),
) -> list[AgentRead]:
    """
    List agents with optional filters.

    Requires admin role.
    """
    return await service.list_agents(job=job, active=active, limit=limit, offset=offset)


@router.post("/", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
async def create_agent(
    agent_data: AgentCreate,
    _current_user: User = Depends(get_current_admin_user),
    service: AgentService = Depends(get_agent_service),
) -> AgentRead:
    """
    Create a new agent.

    Requires admin role.
    """
    return await service.create_agent(agent_data)


@router.get("/{agent_id}", response_model=AgentRead)
async def get_agent(
    agent_id: str,    
    service: AgentService = Depends(get_agent_service),
) -> AgentRead:
    """
    Get an agent by ID.

    Requires admin role.
    """
    return await service.get_agent(agent_id)


@router.patch("/{agent_id}", response_model=AgentRead)
async def update_agent(
    agent_id: str,
    agent_data: AgentUpdate,
    _current_user: User = Depends(get_current_admin_user),
    service: AgentService = Depends(get_agent_service),
) -> AgentRead:
    """
    Update an agent.

    Requires admin role.
    """
    return await service.update_agent(agent_id, agent_data)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: str,
    _current_user: User = Depends(get_current_admin_user),
    service: AgentService = Depends(get_agent_service),
) -> None:
    """
    Delete an agent.

    Requires admin role.
    """
    await service.delete_agent(agent_id)


@router.post("/{agent_id}/ontologies/{ontology_id}", response_model=AgentRead)
async def attach_ontology(
    agent_id: str,
    ontology_id: int,
    _current_user: User = Depends(get_current_admin_user),
    service: AgentService = Depends(get_agent_service),
) -> AgentRead:
    """
    Attach an ontology to an agent.

    Requires admin role.
    """
    return await service.attach_ontology(agent_id, ontology_id)


@router.delete("/{agent_id}/ontologies/{ontology_id}", response_model=AgentRead)
async def detach_ontology(
    agent_id: str,
    ontology_id: int,
    _current_user: User = Depends(get_current_admin_user),
    service: AgentService = Depends(get_agent_service),
) -> AgentRead:
    """
    Detach an ontology from an agent.

    Requires admin role.
    """
    return await service.detach_ontology(agent_id, ontology_id)
