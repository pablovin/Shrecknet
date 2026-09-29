from __future__ import annotations

import pytest

from app.services.ontology_instance_service import OntologyInstanceService


class _Result:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def data(self) -> list[dict]:
        return self.rows


class _Graph:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, query: str, **kwargs):
        del query, kwargs
        self.calls += 1
        return _Result([{"i": {"instance_id": "page-1", "ontology_id": 7, "name": "Page", "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z"}, "entities": [{"entity_instance_id": "entity-1", "entity_definition_id": 4, "alias": "Record", "text": "Full text", "node_avatar_url": "https://example.test/image.png", "created_date": "2026-01-01T00:00:00Z", "last_updated_date": "2026-01-02T00:00:00Z", "author_type": "human", "author_id": "user-1", "properties": "{\"4\": \"value\"}"}], "related_content": [{"id": "page-2", "name": "Related", "image": "https://example.test/related.png"}], "scenes": [{"id": "scene-1", "name": "Arrival", "description": "The arrival scene."}]}])


@pytest.mark.asyncio
async def test_list_instance_pages_returns_complete_content_and_previews() -> None:
    graph = _Graph()
    pages = await OntologyInstanceService(sql_session=None, graph_session=graph).list_instance_pages(ontology_id=7, entity_definition_id=4)
    assert graph.calls == 1
    assert len(pages) == 1
    assert pages[0].entities[0].text == "Full text"
