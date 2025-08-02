"""Shrecknet-specific workers for vector DB and SQL access."""
from __future__ import annotations

import asyncio
from typing import Dict, Iterable, List, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import crud_vectordb, crud_agent_embedding, crud_concept, crud_page
from app.models.model_agent import Agent
from app.models.model_page import Page, PageKeyEvent, PageRelationship
from app.agentic_ai.agentic_worker_utils import (
    normalize_name,
    split_html_by_headers,
    ensure_visible_text,
)


async def async_query_all_embeddings(
    session: AsyncSession,
    agent: Agent,
    query: str,
    n_results: int,
    views: Iterable[str],
    max_chunks_per_page: int,
) -> List[dict]:
    """Query all valid embeddings for a given agent."""
    embeddings = await crud_agent_embedding.get_embeddings(session, agent.id)
    valid_embeds = [e for e in embeddings if e.last_index_time]
    tasks = [
        asyncio.to_thread(
            crud_vectordb.query_world,
            emb.id,
            query,
            n_results,
            views,
            None,
            max_chunks_per_page,
        )
        for emb in valid_embeds
    ]
    all_results = await asyncio.gather(*tasks)
    results: List[dict] = []
    for res_list in all_results:
        for res in res_list:
            res["_from_collection"] = True
            results.append(res)
    return results


def aggregate_prune_and_dedup(
    results: List[dict], n_results: int, max_questions: int
) -> Tuple[str, List[dict]]:
    """Aggregate vector search results and build a context string."""
    seen = set()
    deduped: List[dict] = []
    for r in results:
        if r.get("page_id") is not None and r.get("highlights") and r["highlights"]:
            unique_key = (r.get("page_id"), r["highlights"][0]["chunk_index"])
        else:
            unique_key = r.get("document")
        if unique_key in seen:
            continue
        seen.add(unique_key)
        deduped.append(r)

    deduped.sort(
        key=lambda r: (
            r["highlights"][0]["score"]
            if r.get("highlights") and r["highlights"][0].get("score") is not None
            else 0,
            len(r.get("document", "")),
        ),
        reverse=True,
    )

    selected = deduped[: n_results * max_questions]

    context_blocks = []
    for res in selected:
        subq = res.get("_from_subquestion", "")
        title = res.get("title") or res.get("page_id") or "Untitled"
        context_blocks.append(f"[{subq}] [{title}]: {res['document']}")
    context = "\n\n".join(context_blocks)

    sources = []
    for res in selected:
        sources.append(
            {
                "title": res.get("title") or f"Page {res.get('page_id')}",
                "url": f"/worlds/{res.get('world_id')}/concept/{res.get('concept_id')}/page/{res.get('page_id')}",
                "concept": res.get("concept_name"),
                "concept_id": res.get("concept_id"),
                "page_id": res.get("page_id"),
            }
        )
    return context, sources


async def query_world_embeddings(
    session: AsyncSession,
    agent: Agent,
    sub_questions: List[str],
    n_results: int,
    views: Iterable[str],
    max_chunks_per_page: int,
) -> List[dict]:
    """Run vector searches for a list of questions and annotate the results."""
    tasks = [
        async_query_all_embeddings(
            session, agent, q, n_results * 2, views, max_chunks_per_page
        )
        for q in sub_questions
    ]
    results_lists = await asyncio.gather(*tasks)
    annotated: List[dict] = []
    for q, res_list in zip(sub_questions, results_lists):
        for r in res_list:
            r["_from_subquestion"] = q
            annotated.append(r)
    return annotated


async def load_context_and_data_worker(
    session: AsyncSession, agent: Agent, page: Page
) -> Dict | None:
    """Load concepts, existing pages, and ensure embeddings exist."""
    embeddings = await crud_agent_embedding.get_embeddings(session, agent.id)
    valid_embeds = [e for e in embeddings if e.last_index_time]
    if not valid_embeds:
        return None

    concepts = await crud_concept.get_concepts(
        session, gameworld_id=page.gameworld_id, auto_generated=True
    )
    concept_defs = {c.name: c.description or "" for c in concepts}
    concepts_by_id = {c.id: c for c in concepts}
    concepts_by_name = {normalize_name(c.name): c for c in concepts}

    existing_pages = await crud_page.get_pages(session, gameworld_id=page.gameworld_id)
    existing_titles_norm = {normalize_name(p.name): p for p in existing_pages if p.name}
    # print (f"Existing titles: {existing_titles_norm}")

    return {
        "concept_defs": concept_defs,
        "concepts_by_id": concepts_by_id,
        "concepts_by_name": concepts_by_name,
        "existing_titles_norm": existing_titles_norm,
    }


async def extract_page_metadata_text(page: Page) -> str:
    """Extract events and relationships from a page as plain text."""
    events = getattr(page, "events", None)
    if events is None and hasattr(page, "session"):
        event_result = await page.session.execute(
            select(PageKeyEvent).where(PageKeyEvent.page_id == page.id)
        )
        events = list(event_result.scalars())
    event_lines = [
        f"EVENT [{e.event_type}] on {e.event_date}: {e.summary or ''}"
        for e in (events or [])
    ]

    rels = getattr(page, "relationships", None)
    if rels is None and hasattr(page, "session"):
        rel_result = await page.session.execute(
            select(PageRelationship).where(PageRelationship.page_id == page.id)
        )
        rels = list(rel_result.scalars())
    rel_lines = [
        f"RELATIONSHIP {'->' if r.direction == 'outgoing' else '<-'} {r.target_page_id} ({r.relationship_type}): {r.description or ''}"
        for r in (rels or [])
    ]

    output = ""
    if event_lines:
        output += "Key Events:\n" + "\n".join(event_lines) + "\n\n"
    if rel_lines:
        output += "Relationships:\n" + "\n".join(rel_lines) + "\n\n"
    return output.strip()


async def extract_metadata_and_chunks_worker(page: Page) -> Tuple[str, List[str]]:
    """Extract page metadata and split content into chunk texts."""
    page_metadata_text = await extract_page_metadata_text(page)
    page_chunks = split_html_by_headers(page.content or "")
    chunk_texts = [
        ensure_visible_text(chunk)
        for chunk in page_chunks
        if chunk and chunk.strip()
    ]
    return page_metadata_text, chunk_texts
