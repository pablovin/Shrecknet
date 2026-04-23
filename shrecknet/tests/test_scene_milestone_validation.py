from __future__ import annotations

import pytest

from app.schemas.ontology_instance import MilestoneCreate, SceneCreate
from app.services.ontology_instance_service import OntologyInstanceService


class _FakeResult:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self._rows = rows or []

    async def data(self):
        return self._rows

    async def single(self):
        return self._rows[0] if self._rows else None


class _FakeGraphSession:
    async def run(self, query: str, *args, **kwargs):
        del args
        del kwargs
        if "HAS_ENTITY" in query:
            return _FakeResult(
                rows=[
                    {"entity_instance_id": "entity-1"},
                    {"entity_instance_id": "entity-2"},
                ]
            )
        return _FakeResult(rows=[])


@pytest.mark.asyncio
async def test_validate_scene_milestones_payload_accepts_valid_minimal_scene() -> None:
    service = OntologyInstanceService(sql_session=None, graph_session=_FakeGraphSession())
    payload = SceneCreate(
        id="scene-1",
        name="Opening Scene",
        description="Begins the chapter",
        created_by_type="human",
        created_by_author="user-1",
        derived_from={"entity_instance_id": "entity-1"},
        milestones=[
            MilestoneCreate(
                id="m-1",
                name="Start",
                description="Anchor begin",
                created_by_type="human",
                created_by_author="user-1",
                temporal_type="beginning",
                boundary_type="begin",
                derived_from={"entity_instance_id": "entity-1"},
            ),
            MilestoneCreate(
                id="m-2",
                name="End",
                description="Anchor end",
                created_by_type="human",
                created_by_author="user-1",
                temporal_type="ending",
                boundary_type="end",
                derived_from={"entity_instance_id": "entity-2"},
            ),
        ],
    )

    await service._validate_scene_milestones_payload(
        instance_id="inst-1",
        milestones=payload.milestones,
    )


@pytest.mark.asyncio
async def test_validate_scene_milestones_payload_rejects_missing_end_boundary() -> None:
    service = OntologyInstanceService(sql_session=None, graph_session=_FakeGraphSession())
    milestones = [
        MilestoneCreate(
            id="m-1",
            name="Start",
            description="Anchor begin",
            created_by_type="human",
            created_by_author="user-1",
            temporal_type="beginning",
            boundary_type="begin",
            derived_from={"entity_instance_id": "entity-1"},
        ),
        MilestoneCreate(
            id="m-2",
            name="Middle",
            description="No end boundary",
            created_by_type="human",
            created_by_author="user-1",
            temporal_type="other",
            boundary_type="none",
            derived_from={"entity_instance_id": "entity-2"},
        ),
    ]

    with pytest.raises(ValueError, match="begin boundary milestone and one end"):
        await service._validate_scene_milestones_payload(
            instance_id="inst-1",
            milestones=milestones,
        )


@pytest.mark.asyncio
async def test_validate_scene_milestones_payload_rejects_unknown_derived_from_entity() -> None:
    service = OntologyInstanceService(sql_session=None, graph_session=_FakeGraphSession())
    milestones = [
        MilestoneCreate(
            id="m-1",
            name="Start",
            description="Anchor begin",
            created_by_type="human",
            created_by_author="user-1",
            temporal_type="beginning",
            boundary_type="begin",
            derived_from={"entity_instance_id": "entity-1"},
        ),
        MilestoneCreate(
            id="m-2",
            name="End",
            description="Anchor end",
            created_by_type="human",
            created_by_author="user-1",
            temporal_type="ending",
            boundary_type="end",
            derived_from={"entity_instance_id": "entity-does-not-exist"},
        ),
    ]

    with pytest.raises(ValueError, match="must reference an existing entity"):
        await service._validate_scene_milestones_payload(
            instance_id="inst-1",
            milestones=milestones,
        )
