from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.agentic_ai.agentic_workers import analyze_pages_worker, generate_pages_worker
from app.models.model_agent import Agent
from app.models.model_page import Page


async def analyze_pages(session: AsyncSession, agent: Agent, pages: List[Page]) -> List[dict]:
    """Orchestrate analysis of multiple pages."""
    valid_pages = [p for p in pages if p.gameworld_id == agent.world_id]
    if not valid_pages:
        return []
    return await analyze_pages_worker(session, agent, valid_pages)


async def generate_pages(
    session: AsyncSession,
    agent: Agent,
    page: Page,
    page_specs: List[dict],
) -> dict:
    """Orchestrate generation or update of pages from specs."""
    if page.gameworld_id != agent.world_id:
        raise ValueError("Agent and page belong to different worlds")
    return await generate_pages_worker(session, agent, page, page_specs)
