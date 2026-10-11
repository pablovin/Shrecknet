from __future__ import annotations

import pytest

from app.schemas.ontology_instance import MilestoneCreate, SceneCreate
from app.services.ontology_instance_service import OntologyInstanceService


class _FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    async def single(self):
        return self._rows[0] if self._rows else None

    async def data(self):
        return self._rows


class _RecordingTx:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def run(self, query: str, **kwargs):
        self.calls.append((query, kwargs))
        return _FakeResult()

    async def commit(self):
        return None

    async def rollback(self):
        return None

    async def close(self):
        return None


class _RecordingGraphSession:
    def __init__(self, tx: _RecordingTx):
        self.tx = tx

    async def begin_transaction(self):
        return self.tx

    async def run(self, query: str, **kwargs):
        if "RETURN i.ontology_id AS ontology_id" in query:
            return _FakeResult(rows=[{"ontology_id": 42}])
        if "RETURN scene.id AS scene_id" in query:
            return _FakeResult(rows=[])
        if "RETURN entity.entity_instance_id AS entity_instance_id" in query:
            return _FakeResult(rows=[{"entity_instance_id": "entity-1"}, {"entity_instance_id": "entity-2"}])
        return _FakeResult(rows=[])


@pytest.mark.asyncio
async def test_create_scene_persists_scene_relates_to_edges(monkeypatch):
    tx = _RecordingTx()
    graph = _RecordingGraphSession(tx)
    service = OntologyInstanceService(sql_session=None, graph_session=graph)

    async def _fake_get_scene(instance_id: str, scene_id: str):
        return {
            "instance_id": instance_id,
            "scene_id": scene_id,
        }

    monkeypatch.setattr(service, "get_scene", _fake_get_scene)

    from app.tasks.neo4j_embedding import embed_reconciliation as embed_reconciliation_task

    monkeypatch.setattr(embed_reconciliation_task, "apply_async", lambda **kwargs: None)

    payload = SceneCreate(
        id="scene-1",
        name="Scene One",
        description="Scene description",
        created_by_type="agent",
        created_by_author="tester",
        derived_from={"entity_instance_id": "entity-1"},
        relates_to=[
            {"entity_instance_id": "entity-1", "label": "related_to"},
            {"entity_instance_id": "entity-2", "label": "related_to"},
        ],
        milestones=[],
    )

    await service.create_scene("inst-1", payload)

    relates_calls = [
        call for call in tx.calls if "MERGE (scene)-[:RELATES_TO {label: $label}]->(entity)" in call[0]
    ]
    assert len(relates_calls) == 2


@pytest.mark.asyncio
async def test_create_milestone_persists_milestone_relates_to_edges(monkeypatch):
    tx = _RecordingTx()
    graph = _RecordingGraphSession(tx)
    service = OntologyInstanceService(sql_session=None, graph_session=graph)

    async def _fake_validate_derived(*, instance_id: str, entity_instance_id: str):
        del instance_id, entity_instance_id
        return None

    async def _fake_milestone_ids_for_scene(instance_id: str, scene_id: str):
        del instance_id, scene_id
        return set()

    async def _fake_get_milestone(instance_id: str, scene_id: str, milestone_id: str):
        return {
            "instance_id": instance_id,
            "scene_id": scene_id,
            "milestone_id": milestone_id,
        }

    async def _fake_get_scene(instance_id: str, scene_id: str):
        del instance_id
        return type("_Scene", (), {"id": scene_id, "ontology_id": 42})()

    monkeypatch.setattr(service, "_validate_milestone_derived_from", _fake_validate_derived)
    monkeypatch.setattr(service, "_milestone_ids_for_scene", _fake_milestone_ids_for_scene)
    monkeypatch.setattr(service, "get_scene", _fake_get_scene)
    monkeypatch.setattr(service, "get_milestone", _fake_get_milestone)

    from app.tasks.neo4j_embedding import embed_reconciliation as embed_reconciliation_task

    monkeypatch.setattr(embed_reconciliation_task, "apply_async", lambda **kwargs: None)

    payload = MilestoneCreate(
        id="milestone-1",
        name="Milestone One",
        description="Milestone description",
        created_by_type="agent",
        created_by_author="tester",
        boundary_type="none",
        derived_from={"entity_instance_id": "entity-1"},
        relates_to=[
            {"entity_instance_id": "entity-2", "label": "is_abandoned"},
        ],
    )

    await service.create_milestone("inst-1", "scene-1", payload)

    relates_calls = [
        call for call in tx.calls if "MERGE (milestone)-[:RELATES_TO {label: $label}]->(entity)" in call[0]
    ]
    assert len(relates_calls) == 1


@pytest.mark.asyncio
async def test_scene_event_provenance_is_persisted_and_updates_are_explicit(monkeypatch):
    from app.schemas.ontology_instance import SceneUpdate
    tx = _RecordingTx()
    service = OntologyInstanceService(sql_session=None, graph_session=_RecordingGraphSession(tx))

    async def fake_get_scene(*args):
        return {'id': 'scene-1'}

    monkeypatch.setattr(service, 'get_scene', fake_get_scene)
    from types import SimpleNamespace
    async def milestones(*args):
        return [SimpleNamespace(id='begin', boundary_type='begin'), SimpleNamespace(id='end', boundary_type='end')]
    monkeypatch.setattr(service, 'list_milestones', milestones)
    original_run = tx.run
    async def run(query, **params):
        if 'AS derived_count' in query:
            return _FakeResult(rows=[{'derived_count': 1}])
        return await original_run(query, **params)
    monkeypatch.setattr(tx, 'run', run)
    payload = SceneCreate(id='scene-1', name='Retelling', description='A retelling.',
        created_by_type='human', created_by_author='reviewer',
        derived_from={'entity_instance_id': 'entity-1'},
        observation_group_id='event-42', independence_status='unresolved')
    await service.create_scene('inst-1', payload, trigger_background_jobs=False)
    creation = next(params for query, params in tx.calls if 'CREATE (inst)-[:HAS_SCENE]' in query)
    assert creation['observation_group_id'] == 'event-42'
    assert creation['independence_status'] == 'unresolved'
    from app.tasks.neo4j_embedding import embed_reconciliation as embedding
    monkeypatch.setattr(embedding, 'apply_async', lambda **kwargs: None)
    tx.calls.clear()
    await service.update_scene('inst-1', 'scene-1', SceneUpdate(name='Renamed'))
    assert not any('scene.observation_group_id =' in query for query, _ in tx.calls)
    tx.calls.clear()
    await service.update_scene('inst-1', 'scene-1', SceneUpdate(observation_group_id=None, independence_status='independent'))
    update = next(params for query, params in tx.calls if 'scene.observation_group_id =' in query)
    assert update['observation_group_id'] is None and update['independence_status'] == 'independent'
