from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from shrecknet_client.resources import OntologiesAPI


@pytest.mark.asyncio
async def test_bounded_source_cards_and_scene_search_are_typed():
    request = AsyncMock(side_effect=[
        {"skip": 10, "limit": 10, "has_more": False, "results": [{"id": "page", "display_name": "Chapter", "scene_count": 5}]},
        {"skip": 30, "limit": 30, "total": 100, "results": []},
        {"id": "page", "display_name": "Chapter"},
        [{"id": "elder", "name": "Sage"}],
    ])
    api = OntologiesAPI(SimpleNamespace(raw_request=request))
    assert (await api.scene_sources(7, skip=10)).results[0].scene_count == 5
    assert (
        await api.scenes(
            7,
            query='Arrival',
            related_entity_id='entity',
            related_only=True,
            source_entity_id='anchor',
            skip=30,
        )
    ).total == 100
    assert (await api.scene_source(7, 'scene-1')).id == 'page'
    assert (await api.elders(7))[0].name == 'Sage'
    assert request.call_args_list[0].args == ('GET', '/ontologies/7/scene-sources')
    assert request.call_args_list[1].kwargs['params']['source_entity_id'] == 'anchor'
    assert request.call_args_list[1].kwargs['params']['query'] == 'Arrival'
    assert request.call_args_list[1].kwargs['params']['related_only'] is True


@pytest.mark.asyncio
async def test_folio_reads_preserve_page_identity_and_return_typed_contracts():
    request = AsyncMock(side_effect=[
        {
            "instance_id": "page/1",
            "ontology_id": 7,
            "name": "Chapter",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            "entities": [{
                "entity_instance_id": "node-1",
                "definition_id": 3,
                "alias": "Captain Ada",
                "text": "A captain.",
                "created_date": "2026-01-01T00:00:00Z",
                "last_updated_date": "2026-01-02T00:00:00Z",
                "author_type": "human",
                "author_id": "user-1",
                "properties": [],
                "relationships": [],
            }],
            "scenes": [],
            "folio_projection": {"scenes": "omitted", "relationships": "omitted"},
        },
        [{"id": "user-1", "type": "human", "display_name": "Ada"}],
        {
            "results": [{
                "id": "rel-1",
                "definition_id": 5,
                "direction": "outgoing",
                "focus_entity_id": "node-1",
                "target": {
                    "entity_id": "node-2",
                    "page_id": "page-2",
                    "definition_id": 3,
                    "title": "Doctor Vale",
                },
            }],
            "groups": [],
            "total": 1,
            "skip": 0,
            "limit": 30,
        },
    ])
    api = OntologiesAPI(SimpleNamespace(raw_request=request))

    detail = await api.folio_detail(7, "page/1")
    authors = await api.folio_authors(7, "page/1")
    relationships = await api.folio_relationships(7, "page/1", entity_id="node-1")

    assert detail.instance_id == "page/1"
    assert detail.entities[0].entity_instance_id == "node-1"
    assert detail.folio_projection.scenes == "omitted"
    assert authors[0].display_name == "Ada"
    assert relationships.results[0].target.page_id == "page-2"
    assert request.call_args_list[0].args[1] == "/ontologies/7/pages/page%2F1/folio"
    assert request.call_args_list[1].args[1] == "/ontologies/7/pages/page%2F1/authors"
    assert request.call_args_list[2].args[1] == "/ontologies/7/pages/page%2F1/relationships"
    assert request.call_args_list[2].kwargs["params"] == {
        "skip": 0,
        "limit": 30,
        "entity_id": "node-1",
    }


@pytest.mark.asyncio
async def test_world_web_is_typed_and_preserves_truncation():
    request = AsyncMock(return_value={"ontology_id": 7, "nodes": [{"id": "entity:1", "name": "A", "kind": "entity", "page_id": "page"}], "edges": [], "truncated": True})
    web = await OntologiesAPI(SimpleNamespace(raw_request=request)).world_web(7)
    assert web.nodes[0].page_id == 'page' and web.truncated
    request.assert_awaited_once_with('GET', '/ontologies/7/world-web')


@pytest.mark.asyncio
async def test_folio_patch_only_serializes_supplied_fields():
    from shrecknet_client.atlas import AtlasFolioPatch
    request = AsyncMock(return_value={
        "instance_id": "page-1", "ontology_id": 7, "name": "Changed",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z",
        "entities": [], "folio_projection": {"scenes": "omitted", "relationships": "omitted"},
    })
    api = OntologiesAPI(SimpleNamespace(raw_request=request))
    result = await api.patch_folio(7, "page-1", AtlasFolioPatch(
        expected_updated_at="2026-01-01T00:00:00Z", entity_instance_id="node-1", node_avatar_url=None))
    assert result.name == "Changed"
    args = request.call_args
    assert args.args == ("PATCH", "/ontologies/7/pages/page-1/folio")
    assert args.kwargs["json"] == {"expected_updated_at": "2026-01-01T00:00:00Z", "entity_instance_id": "node-1", "node_avatar_url": None}
