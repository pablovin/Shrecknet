from __future__ import annotations

import pytest

from app.schemas.ontology_instance import OntologyEntityResolveRequest
from app.services.ontology_instance_service import OntologyInstanceService


class _FakeResult:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self._rows = rows or []

    async def data(self):
        return self._rows


class _ResolveGraphSession:
    async def run(self, query: str, *args, **kwargs):
        del args
        if "UNWIND $entity_ids AS entity_id" not in query:
            return _FakeResult(rows=[])

        rows: list[dict] = []
        for entity_id in kwargs.get("entity_ids") or []:
            if entity_id == "uuid-1":
                rows.append(
                    {
                        "entity_instance_id": "uuid-1",
                        "instance_id": "content-instance-uuid",
                        "ontology_id": kwargs.get("ontology_id", 123),
                        "entity_definition_id": 45,
                        "entity_alias": "Eldrin",
                        "instance_name": "Eldrin Profile",
                    }
                )
            if entity_id == "uuid-2":
                rows.append(
                    {
                        "entity_instance_id": "uuid-2",
                        "instance_id": "other-instance-uuid",
                        "ontology_id": kwargs.get("ontology_id", 123),
                        "entity_definition_id": 46,
                        "entity_alias": "Mara",
                        "instance_name": "Mara Profile",
                    }
                )
        return _FakeResult(rows=rows)


@pytest.mark.asyncio
async def test_resolve_entities_returns_ordered_results_and_missing_ids() -> None:
    service = OntologyInstanceService(
        sql_session=None,
        graph_session=_ResolveGraphSession(),
    )

    response = await service.resolve_entities(
        ontology_id=123,
        entity_instance_ids=["uuid-2", "uuid-9", "uuid-1"],
    )

    assert [item.entity_instance_id for item in response.results] == ["uuid-2", "uuid-1"]
    assert response.missing_entity_instance_ids == ["uuid-9"]
    assert response.results[1].instance_id == "content-instance-uuid"
    assert response.results[1].entity_alias == "Eldrin"
    assert response.results[1].instance_name == "Eldrin Profile"


@pytest.mark.asyncio
async def test_resolve_entities_dedupes_ids() -> None:
    service = OntologyInstanceService(
        sql_session=None,
        graph_session=_ResolveGraphSession(),
    )

    response = await service.resolve_entities(
        ontology_id=123,
        entity_instance_ids=["uuid-1", "uuid-1", "uuid-2", "uuid-2"],
    )

    assert [item.entity_instance_id for item in response.results] == ["uuid-1", "uuid-2"]
    assert response.missing_entity_instance_ids == []


def test_resolve_request_rejects_more_than_200_unique_ids() -> None:
    ids = [f"uuid-{index}" for index in range(201)]
    with pytest.raises(ValueError, match="cannot contain more than 200 unique ids"):
        OntologyEntityResolveRequest(
            ontology_id=123,
            entity_instance_ids=ids,
        )
