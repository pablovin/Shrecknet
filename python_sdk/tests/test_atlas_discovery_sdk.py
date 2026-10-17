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
    assert (await api.scenes(7, query='Arrival', source_entity_id='anchor', skip=30)).total == 100
    assert (await api.scene_source(7, 'scene-1')).id == 'page'
    assert (await api.elders(7))[0].name == 'Sage'
    assert request.call_args_list[0].args == ('GET', '/ontologies/7/scene-sources')
    assert request.call_args_list[1].kwargs['params']['source_entity_id'] == 'anchor'
    assert request.call_args_list[1].kwargs['params']['query'] == 'Arrival'
