"""Admin-only ontology CharacterAgent, CharacterAspect and CharacterGoal APIs."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from neo4j import AsyncSession
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from app.api.deps import (
    get_audit_service,
    get_current_admin_user,
    get_current_user,
    get_db_session,
)
from app.api.agent_feature_gate import require_ai_agents_enabled
from app.core.config_store import get_settings, is_shreckllm_configured
from app.db.session import AsyncSessionCompat
from app.db.jobs_session import get_jobs_session
from app.graph.neo4j import get_neo4j_session
from app.models.audit import AuditAction, AuditActorType, AuditEntityType
from app.models.user import User
from app.models.background_job import AuthorType, JobStatus, JobType
from app.models.character_embodiment import CharacterEmbodimentDraft, CharacterEmbodimentDraftStatus
from app.schemas.character_agent import (
    CharacterAgentCreateRequest, CharacterAgentRead, CharacterAgentStatus, CharacterAgentUpdate,
    CharacterAspectAssignmentCreate, CharacterAspectAssignmentRead,
    CharacterAspectAssignmentUpdate, CharacterAspectCreate, CharacterAspectRead,
    CharacterAspectUpdate, CharacterGoalAssignmentCreate, CharacterGoalCreate,
    CharacterGoalRead, CharacterGoalUpdate,
    CharacterEmbodimentCandidatePage,
    CharacterAgentQueryJobRead, CharacterAgentQueryQueued, CharacterAgentQueryRequest,
    EmbodimentDraftCreate, EmbodimentDraftRead, EmbodimentDraftStart,
    CharacterBeliefCreate, CharacterBeliefRead, CharacterBeliefUpdate,
    CharacterImpactCreate, CharacterImpactRead, CharacterImpactUpdate,
    EmotionalInterpretationCreate, EmotionalInterpretationRead,
    EmotionalInterpretationUpdate, ScenePerspectiveAggregateRead,
    ScenePerspectiveCreate, ScenePerspectiveRead, ScenePerspectiveStatus,
    ScenePerspectiveUpdate,
    CharacterIdentityRevisionRead, CharacterIdentityChangeRead,
)
from app.services.audit_service import AuditService
from app.services.background_job_service import BackgroundJobService
from app.services.character_agent_service import CharacterAgentService
from app.services.character_embodiment_service import CharacterEmbodimentService
from app.tasks.character_embodiment import generate_character_embodiment
from app.tasks.character_agent_query import run_character_agent_query
from app.utils.job_tracking import create_background_job
from uuid import uuid4


router = APIRouter(prefix="/character-agents", tags=["character-agents"])
aspect_router = APIRouter(prefix="/character-aspects", tags=["character-aspects"])
goal_router = APIRouter(prefix="/character-goals", tags=["character-goals"])


def _is_admin(user: User) -> bool:
    role = user.role.value if hasattr(user.role, "value") else str(user.role)
    return role.lower() == "admin"


def service(sql_session: AsyncSessionCompat = Depends(get_db_session),
            graph_session: AsyncSession = Depends(get_neo4j_session)) -> CharacterAgentService:
    return CharacterAgentService(sql_session, graph_session)


async def audit_event(audit: AuditService, actor: User, action: AuditAction,
                      entity_type: AuditEntityType, node_id: str) -> None:
    await audit.log_action(
        actor_type=AuditActorType.USER, actor_user_id=actor.id, action=action,
        entity_type=entity_type, payload={"graph_node_id": node_id},
    )


@router.post("", response_model=CharacterAgentRead, status_code=201)
async def create_agent(payload: CharacterAgentCreateRequest, actor: User = Depends(get_current_admin_user),
                       svc: CharacterAgentService = Depends(service), audit: AuditService = Depends(get_audit_service)):
    result = await svc.create_agent(payload, actor.id)
    await audit_event(audit, actor, AuditAction.CREATE, AuditEntityType.CHARACTER_AGENT, result.id)
    return result


@router.get("", response_model=list[CharacterAgentRead])
async def list_agents(ontology_id: int | None = Query(None, ge=1), status_filter: CharacterAgentStatus | None = Query(None, alias="status"),
                      entity_instance_id: str | None = None, skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                      actor: User = Depends(get_current_user), svc: CharacterAgentService = Depends(service)):
    return await svc.list_agents(
        ontology_id, status_filter.value if status_filter else None,
        entity_instance_id, skip, limit, public_only=not _is_admin(actor),
    )


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


async def _draft_or_404(sql: AsyncSessionCompat, draft_id: str) -> CharacterEmbodimentDraft:
    draft = await sql.get(CharacterEmbodimentDraft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Embodiment draft not found")
    return draft


async def _enqueue_draft(draft: CharacterEmbodimentDraft, actor: User) -> int:
    job_id = await create_background_job(
        author_type=AuthorType.USER, author_id=str(actor.id),
        job_type=JobType.CHARACTER_AGENT_EMBODIMENT,
        description=f"Generate CharacterAgent embodiment for {draft.source_entity_id}",
        details={"draft_id": draft.id, "revision": draft.generation_revision},
        ontology_id=draft.ontology_id,
    )
    draft.background_job_id = job_id
    return job_id


@router.post("/embodiment-drafts", response_model=EmbodimentDraftStart, status_code=202)
async def start_embodiment_draft(
    payload: EmbodimentDraftCreate,
    actor: User = Depends(get_current_admin_user),
    sql: AsyncSessionCompat = Depends(get_db_session),
    graph: AsyncSession = Depends(get_neo4j_session),
    svc: CharacterAgentService = Depends(service),
):
    require_ai_agents_enabled()
    settings = get_settings()
    if not is_shreckllm_configured(settings):
        raise HTTPException(status_code=503, detail="shreckLLM is not configured")
    row = await (await graph.run(
        """
        OPTIONAL MATCH (entity:EntityInstance {entity_instance_id:$entity_id})
        OPTIONAL MATCH (agent:CharacterAgent)-[:EMBODIES]->(entity)
        RETURN entity.ontology_id AS ontology_id, entity IS NOT NULL AS exists,
               agent.id AS agent_id
        """, entity_id=payload.entity_instance_id,
    )).single()
    if not row or not row["exists"]:
        raise HTTPException(status_code=404, detail="EntityInstance not found")
    if int(row["ontology_id"] or 0) != payload.ontology_id:
        raise HTTPException(status_code=400, detail="EntityInstance does not belong to ontology")
    if row["agent_id"]:
        await svc.delete_agent(row["agent_id"])
    await sql.execute(delete(CharacterEmbodimentDraft).where(
        CharacterEmbodimentDraft.source_entity_id == payload.entity_instance_id,
        CharacterEmbodimentDraft.status.in_([
            CharacterEmbodimentDraftStatus.QUEUED, CharacterEmbodimentDraftStatus.GENERATING,
            CharacterEmbodimentDraftStatus.READY, CharacterEmbodimentDraftStatus.FAILED,
        ]),
    ))
    draft = CharacterEmbodimentDraft(
        id=str(uuid4()), ontology_id=payload.ontology_id,
        source_entity_id=payload.entity_instance_id, created_by_user_id=actor.id,
        active_entity_key=payload.entity_instance_id,
        status=CharacterEmbodimentDraftStatus.QUEUED, generation_revision=1,
    )
    sql.add(draft)
    try:
        await sql.flush()
    except IntegrityError as exc:
        await sql.rollback()
        raise HTTPException(
            status_code=409, detail="EntityInstance already has an active embodiment draft"
        ) from exc
    job_id = await _enqueue_draft(draft, actor)
    await sql.commit()
    generate_character_embodiment.delay(draft_id=draft.id, revision=1, job_id=job_id)
    return EmbodimentDraftStart(
        draft_id=draft.id, job_id=job_id, status=draft.status,
        draft_url=f"/character-agents/embodiment-drafts/{draft.id}",
        job_url=f"/jobs/{job_id}",
    )


@router.get("/embodiment-drafts/{draft_id}", response_model=EmbodimentDraftRead)
async def get_embodiment_draft(
    draft_id: str, _: User = Depends(get_current_admin_user),
    sql: AsyncSessionCompat = Depends(get_db_session),
):
    return CharacterEmbodimentService.read(await _draft_or_404(sql, draft_id))


@router.get("/{agent_id}", response_model=CharacterAgentRead)
async def get_agent(agent_id: str, actor: User = Depends(get_current_user), svc: CharacterAgentService = Depends(service)):
    return await svc.get_agent(agent_id, public_only=not _is_admin(actor))


@router.get("/{agent_id}/revisions", response_model=list[CharacterIdentityRevisionRead])
async def list_identity_revisions(
    agent_id: str, skip: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
    actor: User = Depends(get_current_user),
    svc: CharacterAgentService = Depends(service),
):
    return await svc.list_identity_revisions(
        agent_id, skip, limit, public_only=not _is_admin(actor)
    )


@router.get("/{agent_id}/identity-changes", response_model=list[CharacterIdentityChangeRead])
async def list_identity_changes(
    agent_id: str,
    change_type: str | None = Query(None, pattern="^(axis|subtitle|aspect|goal)$"),
    skip: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
    actor: User = Depends(get_current_user),
    svc: CharacterAgentService = Depends(service),
):
    return await svc.list_identity_changes(
        agent_id, change_type, skip, limit, public_only=not _is_admin(actor)
    )


@router.post(
    "/{agent_id}/query",
    response_model=CharacterAgentQueryQueued,
    status_code=202,
)
async def query_character_agent(
    agent_id: str,
    payload: CharacterAgentQueryRequest,
    actor: User = Depends(get_current_user),
    svc: CharacterAgentService = Depends(service),
):
    require_ai_agents_enabled()
    settings = get_settings()
    if not is_shreckllm_configured(settings):
        raise HTTPException(status_code=503, detail="shreckLLM is not configured")
    public_only = not _is_admin(actor)
    await svc.ensure_queryable(agent_id, public_only=public_only)
    job_id = await create_background_job(
        author_type=AuthorType.USER,
        author_id=str(actor.id),
        job_type=JobType.CHARACTER_AGENT_QUERY,
        description=f"Query CharacterAgent {agent_id}",
        details={
            "character_agent_id": agent_id,
            "stage": "queued",
            "result": None,
            "error": None,
        },
    )
    run_character_agent_query.delay(
        job_id=job_id,
        agent_id=agent_id,
        request_payload=payload.model_dump(mode="json", by_alias=True),
        public_only=public_only,
    )
    return CharacterAgentQueryQueued(
        job_id=job_id,
        status_url=f"/character-agents/{agent_id}/query-jobs/{job_id}",
    )


@router.get(
    "/{agent_id}/query-jobs/{job_id}",
    response_model=CharacterAgentQueryJobRead,
)
async def get_character_agent_query_job(
    agent_id: str,
    job_id: int,
    actor: User = Depends(get_current_user),
    jobs_session=Depends(get_jobs_session),
):
    job = await BackgroundJobService(jobs_session).get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="CharacterAgent query job not found")
    if job.job_type != JobType.CHARACTER_AGENT_QUERY:
        raise HTTPException(status_code=404, detail="CharacterAgent query job not found")
    if not _is_admin(actor) and (
        job.author_type != AuthorType.USER or str(job.author_id) != str(actor.id)
    ):
        raise HTTPException(status_code=403, detail="Not authorized to read this query job")
    try:
        details = json.loads(job.details or "{}")
    except (TypeError, ValueError):
        details = {}
    if details.get("character_agent_id") != agent_id:
        raise HTTPException(status_code=404, detail="CharacterAgent query job not found")
    status_value = job.status.value if hasattr(job.status, "value") else str(job.status)
    stage = str(details.get("stage") or (
        "completed" if status_value == JobStatus.DONE.value
        else "failed" if status_value == JobStatus.FAILED.value
        else status_value
    ))
    return CharacterAgentQueryJobRead(
        job_id=job.id,
        character_agent_id=agent_id,
        status=status_value,
        stage=stage,
        progress=job.progress,
        result=details.get("result"),
        error=details.get("error"),
        created_at=job.started_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )


@router.get("/{agent_id}/perspectives", response_model=list[ScenePerspectiveRead])
async def list_perspectives(
    agent_id: str,
    status_filter: ScenePerspectiveStatus | None = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    actor: User = Depends(get_current_user),
    svc: CharacterAgentService = Depends(service),
):
    return await svc.list_perspectives(
        agent_id,
        status_filter.value if status_filter else None,
        skip,
        limit,
        public_only=not _is_admin(actor),
    )


@router.post(
    "/{agent_id}/perspectives",
    response_model=ScenePerspectiveAggregateRead,
    status_code=201,
)
async def create_perspective(
    agent_id: str,
    payload: ScenePerspectiveCreate,
    actor: User = Depends(get_current_admin_user),
    svc: CharacterAgentService = Depends(service),
    audit: AuditService = Depends(get_audit_service),
):
    result = await svc.create_perspective(agent_id, payload)
    await audit_event(
        audit, actor, AuditAction.CREATE, AuditEntityType.SCENE_PERSPECTIVE, result.id
    )
    return result


@router.get(
    "/{agent_id}/perspectives/{perspective_id}",
    response_model=ScenePerspectiveAggregateRead,
)
async def get_perspective(
    agent_id: str,
    perspective_id: str,
    actor: User = Depends(get_current_user),
    svc: CharacterAgentService = Depends(service),
):
    return await svc.get_perspective(
        agent_id, perspective_id, public_only=not _is_admin(actor)
    )


@router.patch(
    "/{agent_id}/perspectives/{perspective_id}",
    response_model=ScenePerspectiveAggregateRead,
)
async def update_perspective(
    agent_id: str,
    perspective_id: str,
    payload: ScenePerspectiveUpdate,
    actor: User = Depends(get_current_admin_user),
    svc: CharacterAgentService = Depends(service),
    audit: AuditService = Depends(get_audit_service),
):
    result = await svc.update_perspective(agent_id, perspective_id, payload)
    await audit_event(
        audit, actor, AuditAction.UPDATE, AuditEntityType.SCENE_PERSPECTIVE, perspective_id
    )
    return result


@router.delete("/{agent_id}/perspectives/{perspective_id}", status_code=204)
async def delete_perspective(
    agent_id: str,
    perspective_id: str,
    actor: User = Depends(get_current_admin_user),
    svc: CharacterAgentService = Depends(service),
    audit: AuditService = Depends(get_audit_service),
):
    await svc.delete_perspective(agent_id, perspective_id)
    await audit_event(
        audit, actor, AuditAction.DELETE, AuditEntityType.SCENE_PERSPECTIVE, perspective_id
    )
    return Response(status_code=204)


@router.get(
    "/{agent_id}/perspectives/{perspective_id}/emotions",
    response_model=list[EmotionalInterpretationRead],
)
async def list_perspective_emotions(
    agent_id: str, perspective_id: str, actor: User = Depends(get_current_user),
    svc: CharacterAgentService = Depends(service),
):
    return await svc.list_perspective_children(
        agent_id, perspective_id, "emotions", public_only=not _is_admin(actor)
    )


@router.post(
    "/{agent_id}/perspectives/{perspective_id}/emotions",
    response_model=EmotionalInterpretationRead,
    status_code=201,
)
async def create_perspective_emotion(
    agent_id: str, perspective_id: str, payload: EmotionalInterpretationCreate,
    actor: User = Depends(get_current_admin_user),
    svc: CharacterAgentService = Depends(service),
    audit: AuditService = Depends(get_audit_service),
):
    result = await svc.create_perspective_child(
        agent_id, perspective_id, "emotions", payload
    )
    await audit_event(
        audit, actor, AuditAction.CREATE,
        AuditEntityType.EMOTIONAL_INTERPRETATION, result.id,
    )
    return result


@router.get(
    "/{agent_id}/perspectives/{perspective_id}/emotions/{emotion_id}",
    response_model=EmotionalInterpretationRead,
)
async def get_perspective_emotion(
    agent_id: str, perspective_id: str, emotion_id: str,
    actor: User = Depends(get_current_user),
    svc: CharacterAgentService = Depends(service),
):
    return await svc.get_perspective_child(
        agent_id, perspective_id, emotion_id, "emotions",
        public_only=not _is_admin(actor),
    )


@router.patch(
    "/{agent_id}/perspectives/{perspective_id}/emotions/{emotion_id}",
    response_model=EmotionalInterpretationRead,
)
async def update_perspective_emotion(
    agent_id: str, perspective_id: str, emotion_id: str,
    payload: EmotionalInterpretationUpdate,
    actor: User = Depends(get_current_admin_user),
    svc: CharacterAgentService = Depends(service),
    audit: AuditService = Depends(get_audit_service),
):
    result = await svc.update_perspective_child(
        agent_id, perspective_id, emotion_id, "emotions", payload
    )
    await audit_event(
        audit, actor, AuditAction.UPDATE,
        AuditEntityType.EMOTIONAL_INTERPRETATION, emotion_id,
    )
    return result


@router.delete(
    "/{agent_id}/perspectives/{perspective_id}/emotions/{emotion_id}",
    status_code=204,
)
async def delete_perspective_emotion(
    agent_id: str, perspective_id: str, emotion_id: str,
    actor: User = Depends(get_current_admin_user),
    svc: CharacterAgentService = Depends(service),
    audit: AuditService = Depends(get_audit_service),
):
    await svc.delete_perspective_child(
        agent_id, perspective_id, emotion_id, "emotions"
    )
    await audit_event(
        audit, actor, AuditAction.DELETE,
        AuditEntityType.EMOTIONAL_INTERPRETATION, emotion_id,
    )
    return Response(status_code=204)


@router.get(
    "/{agent_id}/perspectives/{perspective_id}/beliefs",
    response_model=list[CharacterBeliefRead],
)
async def list_perspective_beliefs(
    agent_id: str, perspective_id: str, actor: User = Depends(get_current_user),
    svc: CharacterAgentService = Depends(service),
):
    return await svc.list_perspective_children(
        agent_id, perspective_id, "beliefs", public_only=not _is_admin(actor)
    )


@router.post(
    "/{agent_id}/perspectives/{perspective_id}/beliefs",
    response_model=CharacterBeliefRead,
    status_code=201,
)
async def create_perspective_belief(
    agent_id: str, perspective_id: str, payload: CharacterBeliefCreate,
    actor: User = Depends(get_current_admin_user),
    svc: CharacterAgentService = Depends(service),
    audit: AuditService = Depends(get_audit_service),
):
    result = await svc.create_perspective_child(
        agent_id, perspective_id, "beliefs", payload
    )
    await audit_event(
        audit, actor, AuditAction.CREATE, AuditEntityType.CHARACTER_BELIEF, result.id
    )
    return result


@router.get(
    "/{agent_id}/perspectives/{perspective_id}/beliefs/{belief_id}",
    response_model=CharacterBeliefRead,
)
async def get_perspective_belief(
    agent_id: str, perspective_id: str, belief_id: str,
    actor: User = Depends(get_current_user),
    svc: CharacterAgentService = Depends(service),
):
    return await svc.get_perspective_child(
        agent_id, perspective_id, belief_id, "beliefs",
        public_only=not _is_admin(actor),
    )


@router.patch(
    "/{agent_id}/perspectives/{perspective_id}/beliefs/{belief_id}",
    response_model=CharacterBeliefRead,
)
async def update_perspective_belief(
    agent_id: str, perspective_id: str, belief_id: str,
    payload: CharacterBeliefUpdate,
    actor: User = Depends(get_current_admin_user),
    svc: CharacterAgentService = Depends(service),
    audit: AuditService = Depends(get_audit_service),
):
    result = await svc.update_perspective_child(
        agent_id, perspective_id, belief_id, "beliefs", payload
    )
    await audit_event(
        audit, actor, AuditAction.UPDATE, AuditEntityType.CHARACTER_BELIEF, belief_id
    )
    return result


@router.delete(
    "/{agent_id}/perspectives/{perspective_id}/beliefs/{belief_id}",
    status_code=204,
)
async def delete_perspective_belief(
    agent_id: str, perspective_id: str, belief_id: str,
    actor: User = Depends(get_current_admin_user),
    svc: CharacterAgentService = Depends(service),
    audit: AuditService = Depends(get_audit_service),
):
    await svc.delete_perspective_child(
        agent_id, perspective_id, belief_id, "beliefs"
    )
    await audit_event(
        audit, actor, AuditAction.DELETE, AuditEntityType.CHARACTER_BELIEF, belief_id
    )
    return Response(status_code=204)


@router.get(
    "/{agent_id}/perspectives/{perspective_id}/impacts",
    response_model=list[CharacterImpactRead],
)
async def list_perspective_impacts(
    agent_id: str, perspective_id: str, actor: User = Depends(get_current_user),
    svc: CharacterAgentService = Depends(service),
):
    return await svc.list_perspective_children(
        agent_id, perspective_id, "impacts", public_only=not _is_admin(actor)
    )


@router.post(
    "/{agent_id}/perspectives/{perspective_id}/impacts",
    response_model=CharacterImpactRead,
    status_code=201,
)
async def create_perspective_impact(
    agent_id: str, perspective_id: str, payload: CharacterImpactCreate,
    actor: User = Depends(get_current_admin_user),
    svc: CharacterAgentService = Depends(service),
    audit: AuditService = Depends(get_audit_service),
):
    result = await svc.create_impact(agent_id, perspective_id, payload)
    await audit_event(
        audit, actor, AuditAction.CREATE, AuditEntityType.CHARACTER_IMPACT, result.id
    )
    return result


@router.get(
    "/{agent_id}/perspectives/{perspective_id}/impacts/{impact_id}",
    response_model=CharacterImpactRead,
)
async def get_perspective_impact(
    agent_id: str, perspective_id: str, impact_id: str,
    actor: User = Depends(get_current_user),
    svc: CharacterAgentService = Depends(service),
):
    return await svc.get_perspective_child(
        agent_id, perspective_id, impact_id, "impacts",
        public_only=not _is_admin(actor),
    )


@router.patch(
    "/{agent_id}/perspectives/{perspective_id}/impacts/{impact_id}",
    response_model=CharacterImpactRead,
)
async def update_perspective_impact(
    agent_id: str, perspective_id: str, impact_id: str,
    payload: CharacterImpactUpdate,
    actor: User = Depends(get_current_admin_user),
    svc: CharacterAgentService = Depends(service),
    audit: AuditService = Depends(get_audit_service),
):
    result = await svc.update_perspective_child(
        agent_id, perspective_id, impact_id, "impacts", payload
    )
    await audit_event(
        audit, actor, AuditAction.UPDATE, AuditEntityType.CHARACTER_IMPACT, impact_id
    )
    return result


@router.delete(
    "/{agent_id}/perspectives/{perspective_id}/impacts/{impact_id}",
    status_code=204,
)
async def delete_perspective_impact(
    agent_id: str, perspective_id: str, impact_id: str,
    actor: User = Depends(get_current_admin_user),
    svc: CharacterAgentService = Depends(service),
    audit: AuditService = Depends(get_audit_service),
):
    await svc.delete_perspective_child(
        agent_id, perspective_id, impact_id, "impacts"
    )
    await audit_event(
        audit, actor, AuditAction.DELETE, AuditEntityType.CHARACTER_IMPACT, impact_id
    )
    return Response(status_code=204)


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
async def list_agent_aspects(agent_id: str, actor: User = Depends(get_current_user), svc: CharacterAgentService = Depends(service)):
    return await svc.list_agent_aspects(agent_id, public_only=not _is_admin(actor))


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
async def list_agent_goals(agent_id: str, actor: User = Depends(get_current_user), svc: CharacterAgentService = Depends(service)):
    return await svc.list_agent_goals(agent_id, public_only=not _is_admin(actor))


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
