from difflib import SequenceMatcher
from datetime import datetime, timezone
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

from app.agentic_ai.agentic_workers import (
    load_context_and_data_worker,
    extract_metadata_and_chunks_worker,
    process_chunks_worker,
    merge_and_deduplicate_worker,
    generate_pages_worker,
)
from app.models.model_agent import Agent
from app.models.model_page import Page


async def analyze_pages(session: AsyncSession, agent: Agent, pages: List[Page]) -> List[dict]:
    """Analyze multiple pages and merge suggestions by fuzzy page name."""

    valid_pages = [p for p in pages if p.gameworld_id == agent.world_id]
    if not valid_pages:
        return []

    pages_sorted = sorted(
        valid_pages,
        key=lambda p: (p.updated_at or p.created_at)
        if (p.updated_at or p.created_at)
        else datetime.min,
    )

    all_suggestions: List[dict] = []
    for page in pages_sorted:
        ctx = await load_context_and_data_worker(session, agent, page)
        if not ctx:
            continue
        meta_text, chunks = await extract_metadata_and_chunks_worker(page)
        pairs = await process_chunks_worker(ctx["concept_defs"], meta_text, chunks)
        suggs = merge_and_deduplicate_worker(
            pairs,
            page,
            ctx["existing_titles_norm"],
            ctx["concepts_by_id"],
            ctx["concepts_by_name"],
        )
        all_suggestions.extend(suggs)

    def _similar(a: str, b: str) -> bool:
        return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.6

    merged: List[dict] = []
    for sugg in all_suggestions:
        found = None
        for m in merged:
            if (
                m.get("concept_id") == sugg.get("concept_id")
                and _similar(m["name"], sugg["name"])
            ):
                found = m
                break
        if found:
            existing_ids = {p["id"] for p in found.get("source_pages", [])}
            for sp in sugg.get("source_pages", []):
                if sp["id"] not in existing_ids:
                    found.setdefault("source_pages", []).append(sp)
            found.setdefault("source_page_ids", [])
            for spid in sugg.get("source_page_ids", []):
                if spid not in found["source_page_ids"]:
                    found["source_page_ids"].append(spid)
            cur_dt = sugg.get("source_page_updated", "")
            if cur_dt and cur_dt > found.get("source_page_updated", ""):
                found.update({k: v for k, v in sugg.items() if k != "source_pages"})
        else:
            merged.append(sugg)

    return merged


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

