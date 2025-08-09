import pytest
from unittest.mock import AsyncMock, patch

from app.crud.crud_agent_writer import generate_pages
from app.models.model_agent import Agent
from app.models.model_gameworld import GameWorld
from app.models.model_page import Page
from app.models.model_concept import Concept


@pytest.mark.anyio
async def test_generate_pages_passes_merge_groups(session):
    gw = GameWorld(name="W", system="s", description="d", created_by=1)
    session.add(gw)
    await session.commit()
    await session.refresh(gw)

    concept = Concept(gameworld_id=gw.id, name="C")
    session.add(concept)
    await session.commit()
    await session.refresh(concept)

    agent = Agent(name="A", world_id=gw.id)
    session.add(agent)
    await session.commit()
    await session.refresh(agent)

    page = Page(name="P", gameworld_id=gw.id, concept_id=concept.id)
    session.add(page)
    await session.commit()
    await session.refresh(page)

    page_specs = [{"name": "P", "concept_id": concept.id}]
    merge_groups = [["P", "Alias"]]

    with patch(
        "app.crud.crud_agent_writer.generate_pages_worker", new_callable=AsyncMock
    ) as worker:
        worker.return_value = {"pages": [], "updated": []}
        await generate_pages(session, agent, page, page_specs, merge_groups)
        worker.assert_awaited_once_with(session, agent, page, page_specs, merge_groups)
