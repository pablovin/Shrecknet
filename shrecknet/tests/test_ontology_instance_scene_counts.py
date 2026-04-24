from __future__ import annotations

import pytest

from app.schemas.ontology_instance import OntologyInstanceSceneCountsRequest
from app.services.ontology_instance_service import OntologyInstanceService


class _FakeResult:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self._rows = rows or []

    async def data(self):
        return self._rows


class _SceneCountGraphSession:
    async def run(self, query: str, *args, **kwargs):
        del args
        if "UNWIND $instance_ids AS instance_id" not in query:
            return _FakeResult(rows=[])

        rows: list[dict] = []
        for instance_id in kwargs.get("instance_ids") or []:
            if instance_id == "i-1":
                rows.append({"instance_id": "i-1", "scene_count": 3})
            elif instance_id == "i-2":
                rows.append({"instance_id": "i-2", "scene_count": 0})
        return _FakeResult(rows=rows)


@pytest.mark.asyncio
async def test_count_scenes_by_instances_returns_ordered_results() -> None:
    service = OntologyInstanceService(
        sql_session=None,
        graph_session=_SceneCountGraphSession(),
    )

    response = await service.count_scenes_by_instances(
        instance_ids=["i-2", "i-9", "i-1"],
    )

    assert [item.instance_id for item in response.results] == ["i-2", "i-9", "i-1"]
    assert [item.scene_count for item in response.results] == [0, 0, 3]


def test_scene_counts_request_rejects_more_than_200_unique_ids() -> None:
    ids = [f"i-{index}" for index in range(201)]
    with pytest.raises(ValueError, match="cannot contain more than 200 unique ids"):
        OntologyInstanceSceneCountsRequest(instance_ids=ids)

