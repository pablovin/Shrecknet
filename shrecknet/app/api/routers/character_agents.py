"""Admin-only ontology CharacterAgent, CharacterAspect and CharacterGoal APIs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from neo4j import AsyncSession

from app.api.deps import get_audit_service, get_current_admin_user, get_db_session
from app.api.agent_feature_gate import require_ai_agents_enabled
from app.core.config_store import get_settings, is_shreckllm_configured
from app.db.session import AsyncSessionCompat
from app.graph.neo4j import get_neo4j_session
from app.models.audit import AuditAction, AuditActorType, AuditEntityType
from app.models.user import User
from app.schemas.character_agent import (
    CharacterAgentCreate, CharacterAgentRead, CharacterAgentStatus, CharacterAgentUpdate,
    CharacterAspectAssignmentCreate, CharacterAspectAssignmentRead,
    CharacterAspectAssignmentUpdate, CharacterAspectCreate, CharacterAspectRead,
    CharacterAspectUpdate, CharacterGoalAssignmentCreate, CharacterGoalCreate,
    CharacterGoalRead, CharacterGoalUpdate,
    CharacterEmbodimentCandidatePage,
    CharacterAgentQueryRequest, CharacterAgentQueryResponse,
)
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.jobs.character_agent import CharacterAgentQueryJob, CharacterGenerationError
from app.services.audit_service import AuditService
from app.services.character_agent_service import CharacterAgentService


router = APIRouter(prefix="/character-agents", tags=["character-agents"])
aspect_router = APIRouter(prefix="/character-aspects", tags=["character-aspects"])
goal_router = APIRouter(prefix="/character-goals", tags=["character-goals"])


def service(sql_session: AsyncSessionCompat = Depends(get_db_session),
            graph_session: AsyncSession = Depends(get_neo4j_session)) -> CharacterAgentService:
    return CharacterAgentService(sql_session, graph_session)


async def query_job():
    require_ai_agents_enabled()
    settings = get_settings()
    if not is_shreckllm_configured(settings):
        raise HTTPException(status_code=503, detail="shreckLLM is not configured")
    client = ShreckLLMClient(
        base_url=settings.shreckllm_base_url,
        timeout=settings.shreckllm_request_timeout_s,
        max_retries=settings.shreckllm_max_retries,
    )
    try:
        yield CharacterAgentQueryJob(
            llm_client=client,
            framing_model=settings.model_character_agent_framing,
            deliberation_model=settings.model_character_agent_deliberation,
            verification_model=settings.model_character_agent_verification,
        )
    finally:
        await client.aclose()


async def audit_event(audit: AuditService, actor: User, action: AuditAction,
                      entity_type: AuditEntityType, node_id: str) -> None:
    await audit.log_action(
        actor_type=AuditActorType.USER, actor_user_id=actor.id, action=action,
        entity_type=entity_type, payload={"graph_node_id": node_id},
    )


@router.post("", response_model=CharacterAgentRead, status_code=201)
async def create_agent(payload: CharacterAgentCreate, actor: User = Depends(get_current_admin_user),
                       svc: CharacterAgentService = Depends(service), audit: AuditService = Depends(get_audit_service)):
    result = await svc.create_agent(payload, actor.id)
    await audit_event(audit, actor, AuditAction.CREATE, AuditEntityType.CHARACTER_AGENT, result.id)
    return result


@router.get("", response_model=list[CharacterAgentRead])
async def list_agents(ontology_id: int | None = Query(None, ge=1), status_filter: CharacterAgentStatus | None = Query(None, alias="status"),
                      entity_instance_id: str | None = None, skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                      _: User = Depends(get_current_admin_user), svc: CharacterAgentService = Depends(service)):
    return await svc.list_agents(ontology_id, status_filter.value if status_filter else None, entity_instance_id, skip, limit)


@router.get("/embodiment-candidates", response_model=CharacterEmbodimentCandidatePage)
async def list_embodiment_candidates(
    ontology_id: int = Query(..., ge=1),
    entity_definition_id: int = Query(..., ge=1),
    search: str | None = Query(None, max_length=255),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    _: User = Depends(get_current_admin_user),
    svc: CharacterAgentService = Depends(service),
):
    return await svc.list_embodiment_candidates(
        ontology_id, entity_definition_id, search, skip, limit
    )


@router.get("/{agent_id}", response_model=CharacterAgentRead)
async def get_agent(agent_id: str, _: User = Depends(get_current_admin_user), svc: CharacterAgentService = Depends(service)):
    return await svc.get_agent(agent_id)


@router.post("/{agent_id}/query", response_model=CharacterAgentQueryResponse)
async def query_character_agent(
    agent_id: str,
    payload: CharacterAgentQueryRequest,
    _: User = Depends(get_current_admin_user),
    svc: CharacterAgentService = Depends(service),
    job: CharacterAgentQueryJob = Depends(query_job),
):
    snapshot = await svc.load_query_snapshot(agent_id)
    try:
        return await job.run(payload, snapshot)
    except CharacterGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.patch("/{agent_id}", response_model=CharacterAgentRead)
async def update_agent(agent_id: str, payload: CharacterAgentUpdate, actor: User = Depends(get_current_admin_user),
                       svc: CharacterAgentService = Depends(service), audit: AuditService = Depends(get_audit_service)):
    result = await svc.update_agent(agent_id, payload)
    await audit_event(audit, actor, AuditAction.UPDATE, AuditEntityType.CHARACTER_AGENT, agent_id)
    return result


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: str, actor: User = Depends(get_current_admin_user),
                       svc: CharacterAgentService = Depends(service), audit: AuditService = Depends(get_audit_service)):
    await svc.delete_agent(agent_id)
    await audit_event(audit, actor, AuditAction.DELETE, AuditEntityType.CHARACTER_AGENT, agent_id)
    return Response(status_code=204)


@router.get("/{agent_id}/aspects", response_model=list[CharacterAspectAssignmentRead])
async def list_agent_aspects(agent_id: str, _: User = Depends(get_current_admin_user), svc: CharacterAgentService = Depends(service)):
    return await svc.list_agent_aspects(agent_id)


@router.post("/{agent_id}/aspects", response_model=CharacterAspectAssignmentRead, status_code=201)
async def assign_aspect(agent_id: str, payload: CharacterAspectAssignmentCreate, actor: User = Depends(get_current_admin_user),
                        svc: CharacterAgentService = Depends(service), audit: AuditService = Depends(get_audit_service)):
    result = await svc.assign_aspect(agent_id, payload)
    await audit_event(audit, actor, AuditAction.CREATE, AuditEntityType.CHARACTER_ASPECT_ASSIGNMENT, payload.character_aspect_id)
    return result


@router.patch("/{agent_id}/aspects/{aspect_id}", response_model=CharacterAspectAssignmentRead)
async def update_assignment(agent_id: str, aspect_id: str, payload: CharacterAspectAssignmentUpdate,
                            actor: User = Depends(get_current_admin_user), svc: CharacterAgentService = Depends(service),
                            audit: AuditService = Depends(get_audit_service)):
    result = await svc.update_assignment(agent_id, aspect_id, payload)
    await audit_event(audit, actor, AuditAction.UPDATE, AuditEntityType.CHARACTER_ASPECT_ASSIGNMENT, aspect_id)
    return result


@router.delete("/{agent_id}/aspects/{aspect_id}", status_code=204)
async def unassign_aspect(agent_id: str, aspect_id: str, actor: User = Depends(get_current_admin_user),
                          svc: CharacterAgentService = Depends(service), audit: AuditService = Depends(get_audit_service)):
    await svc.unassign(agent_id, aspect_id, "CharacterAspect", "HAS_ASPECT")
    await audit_event(audit, actor, AuditAction.DELETE, AuditEntityType.CHARACTER_ASPECT_ASSIGNMENT, aspect_id)
    return Response(status_code=204)


@router.get("/{agent_id}/goals", response_model=list[CharacterGoalRead])
async def list_agent_goals(agent_id: str, _: User = Depends(get_current_admin_user), svc: CharacterAgentService = Depends(service)):
    return await svc.list_agent_goals(agent_id)


@router.post("/{agent_id}/goals", response_model=CharacterGoalRead, status_code=201)
async def pursue_goal(agent_id: str, payload: CharacterGoalAssignmentCreate, actor: User = Depends(get_current_admin_user),
                      svc: CharacterAgentService = Depends(service), audit: AuditService = Depends(get_audit_service)):
    result = await svc.pursue_goal(agent_id, payload.character_goal_id)
    await audit_event(audit, actor, AuditAction.CREATE, AuditEntityType.CHARACTER_GOAL_PURSUIT, payload.character_goal_id)
    return result


@router.delete("/{agent_id}/goals/{goal_id}", status_code=204)
async def stop_goal(agent_id: str, goal_id: str, actor: User = Depends(get_current_admin_user),
                    svc: CharacterAgentService = Depends(service), audit: AuditService = Depends(get_audit_service)):
    await svc.unassign(agent_id, goal_id, "CharacterGoal", "PURSUES")
    await audit_event(audit, actor, AuditAction.DELETE, AuditEntityType.CHARACTER_GOAL_PURSUIT, goal_id)
    return Response(status_code=204)


@aspect_router.post("", response_model=CharacterAspectRead, status_code=201)
async def create_aspect(payload: CharacterAspectCreate, actor: User = Depends(get_current_admin_user), svc: CharacterAgentService = Depends(service), audit: AuditService = Depends(get_audit_service)):
    result = await svc.create_aspect(payload, actor.id); await audit_event(audit, actor, AuditAction.CREATE, AuditEntityType.CHARACTER_ASPECT, result.id); return result


@aspect_router.get("", response_model=list[CharacterAspectRead])
async def list_aspects(ontology_id: int = Query(..., ge=1), skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), _: User = Depends(get_current_admin_user), svc: CharacterAgentService = Depends(service)):
    return await svc.list_aspects(ontology_id, skip, limit)


@aspect_router.get("/{aspect_id}", response_model=CharacterAspectRead)
async def get_aspect(aspect_id: str, _: User = Depends(get_current_admin_user), svc: CharacterAgentService = Depends(service)): return await svc.get_aspect(aspect_id)


@aspect_router.patch("/{aspect_id}", response_model=CharacterAspectRead)
async def update_aspect(aspect_id: str, payload: CharacterAspectUpdate, actor: User = Depends(get_current_admin_user), svc: CharacterAgentService = Depends(service), audit: AuditService = Depends(get_audit_service)):
    result = await svc.update_aspect(aspect_id, payload); await audit_event(audit, actor, AuditAction.UPDATE, AuditEntityType.CHARACTER_ASPECT, aspect_id); return result


@aspect_router.delete("/{aspect_id}", status_code=204)
async def delete_aspect(aspect_id: str, actor: User = Depends(get_current_admin_user), svc: CharacterAgentService = Depends(service), audit: AuditService = Depends(get_audit_service)):
    await svc.delete_definition("CharacterAspect", aspect_id); await audit_event(audit, actor, AuditAction.DELETE, AuditEntityType.CHARACTER_ASPECT, aspect_id); return Response(status_code=204)


@goal_router.post("", response_model=CharacterGoalRead, status_code=201)
async def create_goal(payload: CharacterGoalCreate, actor: User = Depends(get_current_admin_user), svc: CharacterAgentService = Depends(service), audit: AuditService = Depends(get_audit_service)):
    result = await svc.create_goal(payload, actor.id); await audit_event(audit, actor, AuditAction.CREATE, AuditEntityType.CHARACTER_GOAL, result.id); return result


@goal_router.get("", response_model=list[CharacterGoalRead])
async def list_goals(ontology_id: int = Query(..., ge=1), skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), _: User = Depends(get_current_admin_user), svc: CharacterAgentService = Depends(service)):
    return await svc.list_goals(ontology_id, skip, limit)


@goal_router.get("/{goal_id}", response_model=CharacterGoalRead)
async def get_goal(goal_id: str, _: User = Depends(get_current_admin_user), svc: CharacterAgentService = Depends(service)): return await svc.get_goal(goal_id)


@goal_router.patch("/{goal_id}", response_model=CharacterGoalRead)
async def update_goal(goal_id: str, payload: CharacterGoalUpdate, actor: User = Depends(get_current_admin_user), svc: CharacterAgentService = Depends(service), audit: AuditService = Depends(get_audit_service)):
    result = await svc.update_goal(goal_id, payload); await audit_event(audit, actor, AuditAction.UPDATE, AuditEntityType.CHARACTER_GOAL, goal_id); return result


@goal_router.delete("/{goal_id}", status_code=204)
async def delete_goal(goal_id: str, actor: User = Depends(get_current_admin_user), svc: CharacterAgentService = Depends(service), audit: AuditService = Depends(get_audit_service)):
    await svc.delete_definition("CharacterGoal", goal_id); await audit_event(audit, actor, AuditAction.DELETE, AuditEntityType.CHARACTER_GOAL, goal_id); return Response(status_code=204)
