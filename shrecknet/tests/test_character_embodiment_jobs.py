from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.routers import character_agents
from app.models.character_embodiment import CharacterEmbodimentDraftStatus
from app.schemas.character_agent import (
    CharacterAgentEmbodimentUpdate,
    CharacterTimelineProjection,
    EmbodimentDraftCreate,
)
from app.schemas.character_traits import TraitProfile
from app.services.character_agent_service import CharacterAgentService


class _GraphResult:
    def __init__(self, value):
        self.value = value

    async def single(self):
        return self.value


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return self

    def first(self):
        return self.value

    def all(self):
        return self.value


class _Sql:
    def __init__(self, draft):
        self.draft = draft
        self.deleted = False
        self.added = None
        self.committed = False

    async def execute(self, _statement):
        return _ScalarResult(self.draft)

    async def delete(self, _draft):
        self.deleted = True

    async def flush(self):
        pass

    def add(self, draft):
        self.added = draft

    async def commit(self):
        self.committed = True


class _ListSql:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, _statement):
        return _ScalarResult(self.rows)


class _Graph:
    def __init__(self, agent_id):
        self.agent_id = agent_id

    async def run(self, _query, *, entity_id):
        assert entity_id == "entity-1"
        return _GraphResult({"exists": True, "ontology_id": 7, "agent_id": self.agent_id})


class _UpdateTx:
    def __init__(self, agent):
        self.agent = agent

    async def run(self, query, **_params):
        if "RETURN agent, entity.entity_instance_id AS entity_id" in query:
            return _GraphResult({"agent": self.agent, "entity_id": "entity-1"})
        return _GraphResult(None)


class _UpdateGraph:
    def __init__(self, agent):
        self.agent = agent

    async def execute_write(self, callback):
        return await callback(_UpdateTx(self.agent))


class _UpdateSql:
    def __init__(self, draft):
        self.draft = draft
        self.committed = False

    async def get(self, _model, _draft_id):
        return self.draft

    async def commit(self):
        self.committed = True


class _DraftSql:
    def __init__(self, draft):
        self.draft = draft
        self.deleted = False
        self.committed = False

    async def get(self, _model, _draft_id):
        return self.draft

    async def delete(self, _draft):
        self.deleted = True

    async def commit(self):
        self.committed = True


def _active_draft(target_agent_id="agent-1"):
    return SimpleNamespace(
        id="draft-1",
        created_by_user_id=11,
        status=CharacterEmbodimentDraftStatus.GENERATING,
        active_entity_key="entity-1",
        target_character_agent_id=target_agent_id,
        background_job_id=19,
    )


@pytest.mark.asyncio
async def test_starting_again_returns_the_existing_active_draft(monkeypatch):
    monkeypatch.setattr(character_agents, "require_ai_agents_enabled", lambda: None)
    monkeypatch.setattr(character_agents, "get_settings", lambda: object())
    monkeypatch.setattr(character_agents, "is_shreckllm_configured", lambda _settings: True)
    sql = _Sql(_active_draft())

    started = await character_agents.start_embodiment_draft(
        EmbodimentDraftCreate(
            ontology_id=7,
            entity_instance_id="entity-1",
            target_character_agent_id="agent-1",
        ),
        SimpleNamespace(id=11),
        sql,
        _Graph("agent-1"),
        None,
    )

    assert started.draft_id == "draft-1"
    assert started.job_id == 19
    assert started.status == CharacterEmbodimentDraftStatus.GENERATING
    assert not sql.deleted


@pytest.mark.asyncio
async def test_starting_creation_for_an_embodied_entity_never_deletes_the_agent(monkeypatch):
    monkeypatch.setattr(character_agents, "require_ai_agents_enabled", lambda: None)
    monkeypatch.setattr(character_agents, "get_settings", lambda: object())
    monkeypatch.setattr(character_agents, "is_shreckllm_configured", lambda _settings: True)

    with pytest.raises(HTTPException) as raised:
        await character_agents.start_embodiment_draft(
            EmbodimentDraftCreate(ontology_id=7, entity_instance_id="entity-1"),
            SimpleNamespace(id=11),
            _Sql(None),
            _Graph("agent-1"),
            None,
        )

    assert raised.value.status_code == 409
    assert "already embodied" in str(raised.value.detail)


@pytest.mark.asyncio
async def test_confirmed_replacement_reuses_ready_draft_with_fresh_generation(monkeypatch):
    monkeypatch.setattr(character_agents, "require_ai_agents_enabled", lambda: None)
    monkeypatch.setattr(character_agents, "get_settings", lambda: object())
    monkeypatch.setattr(character_agents, "is_shreckllm_configured", lambda _settings: True)
    async def create_job(**_kwargs):
        return 20
    monkeypatch.setattr(character_agents, "create_background_job", create_job)
    delayed = []
    monkeypatch.setattr(character_agents.generate_character_embodiment, "delay", lambda **kwargs: delayed.append(kwargs))
    previous = _active_draft()
    previous.status = CharacterEmbodimentDraftStatus.READY
    previous.generation_revision = 1
    previous.generation_checkpoints = '{"source:0":{"version":1}}'
    sql = _Sql(previous)

    started = await character_agents.start_embodiment_draft(
        EmbodimentDraftCreate(
            ontology_id=7, entity_instance_id="entity-1",
            target_character_agent_id="agent-1", replace_existing=True,
        ),
        SimpleNamespace(id=11), sql, _Graph("agent-1"), None,
    )

    assert not sql.deleted
    assert sql.committed
    assert started.draft_id == previous.id
    assert previous.generation_revision == 2
    assert previous.generation_checkpoints is None
    assert started.job_id == 20
    assert delayed == [{"draft_id": started.draft_id, "revision": 2, "job_id": 20}]


@pytest.mark.asyncio
async def test_confirmed_retry_preserves_draft_checkpoints_and_increments_revision(monkeypatch):
    monkeypatch.setattr(character_agents, "require_ai_agents_enabled", lambda: None)
    monkeypatch.setattr(character_agents, "get_settings", lambda: object())
    monkeypatch.setattr(character_agents, "is_shreckllm_configured", lambda _settings: True)
    async def create_job(**_kwargs):
        return 21
    monkeypatch.setattr(character_agents, "create_background_job", create_job)
    delayed = []
    monkeypatch.setattr(character_agents.generate_character_embodiment, "delay", lambda **kwargs: delayed.append(kwargs))
    previous = _active_draft()
    previous.status = CharacterEmbodimentDraftStatus.FAILED
    previous.generation_revision = 1
    previous.generation_checkpoints = '{"source:0":{"version":1}}'
    sql = _Sql(previous)

    started = await character_agents.start_embodiment_draft(
        EmbodimentDraftCreate(
            ontology_id=7, entity_instance_id="entity-1",
            target_character_agent_id="agent-1", replace_existing=True,
        ),
        SimpleNamespace(id=11), sql, _Graph("agent-1"), None,
    )

    assert started.draft_id == previous.id
    assert started.job_id == 21
    assert previous.generation_revision == 2
    assert previous.generation_checkpoints == '{"source:0":{"version":1}}'
    assert previous.status == CharacterEmbodimentDraftStatus.QUEUED
    assert not sql.deleted
    assert delayed == [{"draft_id": previous.id, "revision": 2, "job_id": 21}]


def test_embodiment_start_contract_supports_update_and_confirmed_replacement():
    payload = EmbodimentDraftCreate(
        ontology_id=7,
        entity_instance_id="entity-1",
        target_character_agent_id="agent-1",
        replace_existing=True,
    )

    assert payload.target_character_agent_id == "agent-1"
    assert payload.replace_existing


def test_character_agent_update_contract_accepts_reviewed_draft_payload():
    update = CharacterAgentEmbodimentUpdate(
        embodiment_draft_id="draft-1",
        aspects=[{"name": "I protect the group", "category": "attitude", "in_focus": True}],
        goals=[{"title": "Keep the group safe", "goal_type": "obligation"}],
    )

    assert update.embodiment_draft_id == "draft-1"
    assert update.aspects[0].name == "I protect the group"
    assert update.goals[0].title == "Keep the group safe"


@pytest.mark.asyncio
async def test_list_embodiment_drafts_returns_resume_metadata_for_current_admin():
    now = datetime.now(timezone.utc)
    draft = SimpleNamespace(
        id="draft-1", ontology_id=7, source_entity_id="entity-1",
        target_character_agent_id="agent-1", status=CharacterEmbodimentDraftStatus.READY,
        background_job_id=19, error_message=None, created_at=now, updated_at=now,
    )

    summaries = await character_agents.list_embodiment_drafts(
        ontology_id=7, limit=20, actor=SimpleNamespace(id=11), sql=_ListSql([draft]),
    )

    assert len(summaries) == 1
    assert summaries[0].id == "draft-1"
    assert summaries[0].background_job_id == 19
    assert summaries[0].target_character_agent_id == "agent-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [CharacterEmbodimentDraftStatus.READY, CharacterEmbodimentDraftStatus.FAILED])
async def test_admin_can_delete_owned_terminal_embodiment_draft(status):
    draft = _active_draft()
    draft.status = status
    sql = _DraftSql(draft)

    response = await character_agents.delete_embodiment_draft(
        "draft-1", SimpleNamespace(id=11), sql,
    )

    assert response.status_code == 204
    assert sql.deleted
    assert sql.committed


@pytest.mark.asyncio
async def test_embodiment_draft_delete_hides_drafts_owned_by_another_admin():
    sql = _DraftSql(_active_draft())

    with pytest.raises(HTTPException) as raised:
        await character_agents.delete_embodiment_draft(
            "draft-1", SimpleNamespace(id=12), sql,
        )

    assert raised.value.status_code == 404
    assert not sql.deleted


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [
    CharacterEmbodimentDraftStatus.QUEUED,
    CharacterEmbodimentDraftStatus.GENERATING,
    CharacterEmbodimentDraftStatus.ACCEPTED,
])
async def test_embodiment_draft_delete_rejects_non_deletable_statuses(status):
    draft = _active_draft()
    draft.status = status
    sql = _DraftSql(draft)

    with pytest.raises(HTTPException) as raised:
        await character_agents.delete_embodiment_draft(
            "draft-1", SimpleNamespace(id=11), sql,
        )

    assert raised.value.status_code == 409
    assert not sql.deleted


@pytest.mark.asyncio
async def test_reviewed_update_appends_once_without_replacing_the_agent():
    timeline = CharacterTimelineProjection.model_validate({
        "revisions": [{
            "revision_number": 0, "name": "Mara", "subtitle": None,
            "trait_profile": TraitProfile().model_dump(mode="json"),
            "active_aspects": [], "active_goals": [],
        }],
        "source_projections": [],
    })
    draft = SimpleNamespace(
        id="draft-1", status=CharacterEmbodimentDraftStatus.READY,
        target_character_agent_id="agent-1", created_by_user_id=11,
        ontology_id=7, source_entity_id="entity-1", timeline_projection=timeline.model_dump_json(),
        source_evidence_ids="[]", provider="provider", model="model", prompt_version="v1",
        active_entity_key="entity-1", generated_proposal="{}",
    )
    agent = {
        "id": "agent-1", "ontology_id": 7, "embodied_entity_instance_id": "entity-1",
        "name": "Mara", "background_story": "Old story", "status": "active",
        "visibility": "private", "created_by_user_id": 11,
        "created_at": "2026-10-08T10:00:00+00:00", "updated_at": "2026-10-08T10:00:00+00:00",
    }
    sql = _UpdateSql(draft)
    service = CharacterAgentService(sql, _UpdateGraph(agent))
    service._persist_timeline_tx = AsyncMock()
    service._create_revision_tx = AsyncMock()
    service._record_manual_traits_tx = AsyncMock()
    service.list_perspectives = AsyncMock(return_value=[])
    service.refresh_perspective_memory = AsyncMock()

    updated = await service.update_agent(
        "agent-1",
        CharacterAgentEmbodimentUpdate(
            embodiment_draft_id="draft-1", name="Mara reviewed", aspects=[], goals=[],
        ),
        user_id=11,
    )

    assert updated.id == "agent-1"
    assert updated.name == "Mara reviewed"
    assert sql.committed
    assert draft.status == CharacterEmbodimentDraftStatus.ACCEPTED
    assert draft.active_entity_key is None
    service._persist_timeline_tx.assert_awaited_once()


@pytest.mark.asyncio
async def test_embodiment_job_progress_uses_three_consistent_source_steps(monkeypatch):
    from app.tasks import character_embodiment

    published = []

    async def capture(_job_id, _progress, details):
        published.append(details)

    monkeypatch.setattr(character_embodiment, "update_job_progress", capture)
    progress = character_embodiment._EmbodimentProgress(
        job_id=19, draft_id="draft-1", bundles=[{}],
        source_groups=[{"source_alias": "Source"}],
    )
    progress.configure_chunks(0, [[{"scene_id": "scene-1"}]])
    await progress.stage(0, [1], chunk_index=0)
    await progress.stage(0, [2], chunk_index=0)
    await progress.chunk_complete(0, 0)
    await progress.stage(0, [3])
    assert published[-1]["bundles"][0]["parallel"]["active_branches"] == [
        "Trait interpretation and aggregation"
    ]
    await progress.complete(0)
    bundle = published[-1]["bundles"][0]
    assert bundle["done_steps"] == [1, 2, 3]
    assert bundle["active_steps"] == []
    assert bundle["chunks"][0]["done_steps"] == [1, 2, 3, 4]
