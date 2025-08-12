from __future__ import annotations

import asyncio
import json
from difflib import SequenceMatcher
from datetime import datetime, timezone
from typing import Dict, List, Optional

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.agentic_ai.agentic_worker_shrecknet import (
    load_context_and_data_worker,
    extract_metadata_and_chunks_worker,
)
from app.agentic_ai.agentic_worker_llm import (
    process_chunks_worker,
    merge_and_deduplicate_worker,
    generate_process_source_worker,
)
from app.agentic_ai.agentic_worker_utils import (
    normalize_name,
    strip_html,
)
from app.crud import crud_concept, crud_page, crud_world_embedding, crud_vectordb
from app.models.model_agent import Agent
from app.models.model_page import Page, PageKeyEvent, PageRelationship


async def analyze_pages(
    session: AsyncSession, agent: Agent, pages: List[Page]
) -> List[dict]:
    """Analyze multiple pages and merge suggestions by fuzzy page name."""

    valid_pages = [p for p in pages if p.gameworld_id == agent.world_id]
    if not valid_pages:
        return []

    pages_sorted = sorted(
        valid_pages,
        key=lambda p: (
            (p.updated_at or p.created_at)
            if (p.updated_at or p.created_at)
            else datetime.min
        ),
    )

    all_suggestions: List[dict] = []
    for page in pages_sorted:
        ctx = await load_context_and_data_worker(session, agent, page)
        if not ctx:
            continue
        meta_text, chunks = await extract_metadata_and_chunks_worker(page)
        pairs = await process_chunks_worker(ctx["concept_defs"], meta_text, chunks)
        # print (f"Pairs: {pairs}")
        # print (f"existing_titles_norm: {ctx['existing_titles_norm']}")
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
            if m.get("concept_id") == sugg.get("concept_id") and _similar(
                m["name"], sugg["name"]
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


async def generate_pages_worker(
    session: AsyncSession,
    agent: Agent,
    page: Page,
    page_specs: List[dict],
    merge_groups: Optional[List[List[str]]] = None,
) -> dict:
    """Generate or update pages based on specifications."""

    # Attach alias names from merge groups so the LLM can treat them as the same page
    if merge_groups:
        norm_to_spec = {normalize_name(s["name"]): s for s in page_specs}
        for group in merge_groups:
            base_spec = None
            for name in group:
                ns = normalize_name(name)
                if ns in norm_to_spec:
                    base_spec = norm_to_spec[ns]
                    break
            if base_spec:
                aliases = [
                    n
                    for n in group
                    if normalize_name(n) != normalize_name(base_spec["name"])
                ]
                if aliases:
                    base_spec["aliases"] = aliases

    create_specs = [s for s in page_specs if s.get("mode", "create") == "create"]
    update_specs = [s for s in page_specs if s.get("mode") == "update"]
    all_specs = create_specs + update_specs
    if not all_specs:
        return {"pages": [], "updated": []}

    concept_ids = {s["concept_id"] for s in all_specs}
    concepts = await crud_concept.get_concepts(session)
    concept_map = {c.id: c for c in concepts if c.id in concept_ids}

    all_pages = await crud_page.get_pages(session)
    page_map = {p.id: p for p in all_pages}
    name_to_page = {normalize_name(p.name): p for p in all_pages}

    sources_map: Dict[int, List[dict]] = {}
    for spec in all_specs:
        for pid in spec.get("source_page_ids", []):
            if pid in page_map:
                sources_map.setdefault(pid, []).append(spec)

    if all_specs and not sources_map:
        return {"pages": [], "updated": []}

    llm = ChatOpenAI(api_key=settings.openai_api_key, model=settings.open_ai_model)

    spec_data: Dict[int, Dict[str, list]] = {
        id(spec): {"parts": [], "events": [], "rels": [], "spec": spec}
        for spec in all_specs
    }

    if sources_map:
        await asyncio.gather(
            *(
                generate_process_source_worker(
                    page_map[pid], specs, llm, concept_map, spec_data
                )
                for pid, specs in sources_map.items()
            )
        )

    created_pages: List[Page] = []
    new_name_to_page: Dict[str, Page] = {}
    for sid, info in spec_data.items():
        spec = info["spec"]
        if spec.get("mode", "create") != "create":
            continue
        concept = concept_map.get(spec["concept_id"])
        if not concept:
            continue
        source_ids = spec.get("source_page_ids", [page.id])
        gameworld_id = (
            page_map[source_ids[0]].gameworld_id if source_ids else page.gameworld_id
        )
        final_text = "\n\n".join(info["parts"])
        new_page = Page(
            name=spec["name"],
            gameworld_id=gameworld_id,
            concept_id=concept.id,
            allow_crosslinks=True,
            ignore_crosslink=False,
            allow_crossworld=True,
            updated_by_agent_id=agent.id,
            autogenerated_content=final_text,
        )
        await crud_page.create_page(session, new_page)
        created_pages.append(new_page)
        new_name_to_page[normalize_name(new_page.name)] = new_page

    all_name_to_page = {**name_to_page, **new_name_to_page}

    today_dt = datetime.now(timezone.utc).date()
    results: List[dict] = []
    updated: List[dict] = []

    for sid, info in spec_data.items():
        spec = info["spec"]
        if spec.get("mode", "create") != "create":
            continue
        page_obj = new_name_to_page.get(normalize_name(spec["name"]))
        if not page_obj:
            continue

        unique_events = {json.dumps(ev, sort_keys=True) for ev in info["events"]}

        seen_targets: set[str] = set()

        for ev_json in unique_events:
            ev = json.loads(ev_json)
            ev.update(
                {
                    "page_id": page_obj.id,
                    "author_type": "agent",
                    "author_id": agent.id,
                }
            )
            ev_date = ev.get("event_date")
            if isinstance(ev_date, str):
                try:
                    ev_date = datetime.fromisoformat(ev_date)
                except ValueError:
                    ev_date = None
            if not ev_date:
                ev_date = datetime.combine(
                    today_dt, datetime.min.time(), tzinfo=timezone.utc
                )
            ev["event_date"] = ev_date
            related_names = ev.get("related_pages", [])
            related_ids: List[int] = []
            for name in related_names:
                target_page = all_name_to_page.get(normalize_name(name))
                if target_page:
                    related_ids.append(target_page.id)
            ev["related_page_ids"] = related_ids
            ev.pop("related_pages", None)

            await crud_page.create_key_event(session, PageKeyEvent(**ev))

        for rel in info["rels"]:
            target_name = rel.get("target_page")
            norm_name = normalize_name(target_name) if target_name else None
            if norm_name and norm_name in seen_targets:
                continue
            if norm_name:
                seen_targets.add(norm_name)
            rel.update(
                {
                    "page_id": page_obj.id,
                    "author_type": "agent",
                    "author_id": agent.id,
                }
            )
            target_page = all_name_to_page.get(norm_name) if norm_name else None
            if target_page:
                rel["target_page_id"] = target_page.id
                rel.pop("target_page", None)
                await crud_page.create_relationship(session, PageRelationship(**rel))
            else:
                print(
                    f"[generate_pages] Discarded relationship without valid target_page_id: {rel}"
                )

        results.append({"name": page_obj.name, "id": page_obj.id})

    if update_specs:
        update_llm = ChatOpenAI(
            api_key=settings.openai_api_key, model=settings.open_ai_model
        )

        rels_cache: Dict[int, List[PageRelationship]] = {}

        async def remove_existing_relationships(pid1: int, pid2: int) -> None:
            rels1 = rels_cache.get(pid1)
            if rels1 is None:
                rels1 = await crud_page.get_relationships(session, pid1)
                rels_cache[pid1] = rels1
            for r in rels1[:]:
                if r.target_page_id == pid2:
                    await crud_page.delete_relationship(session, r.id)
                    rels1.remove(r)
            rels2 = rels_cache.get(pid2)
            if rels2 is None:
                rels2 = await crud_page.get_relationships(session, pid2)
                rels_cache[pid2] = rels2
            for r in rels2[:]:
                if r.target_page_id == pid1:
                    await crud_page.delete_relationship(session, r.id)
                    rels2.remove(r)

        for sid, info in spec_data.items():
            spec = info["spec"]
            if spec.get("mode") != "update":
                continue
            target_page = page_map.get(spec.get("target_page_id"))
            if not target_page and spec.get("name"):
                target_page = all_name_to_page.get(normalize_name(spec.get("name")))
            concept = concept_map.get(spec.get("concept_id"))
            if not target_page or not concept:
                continue

            new_text = "\n\n".join(info["parts"])
            existing_text = (
                target_page.autogenerated_content or target_page.content or ""
            )
            if new_text and existing_text:
                merge_prompt = ChatPromptTemplate.from_messages(
                    [
                        (
                            "system",
                            "You are an expert RPG campaign chronicler. Combine the existing page content and the new content into a single coherent text.",
                        ),
                        (
                            "user",
                            "Existing content:\n{existing}\n\nNew content:\n{new}",
                        ),
                    ]
                )
                chain = merge_prompt | update_llm
                try:
                    resp = await chain.ainvoke(
                        {
                            "existing": strip_html(existing_text),
                            "new": new_text,
                        }
                    )
                    combined_text = resp.content.strip()
                except Exception as e:
                    print(f"[update_page] LLM error: {e}")
                    combined_text = "\n\n".join(
                        [t for t in [existing_text, new_text] if t]
                    )
            else:
                combined_text = new_text or existing_text

            await crud_page.update_page(
                session,
                target_page.id,
                {
                    "autogenerated_content": combined_text,
                    "updated_by_agent_id": agent.id,
                },
            )

            existing_events = await crud_page.get_key_events(session, target_page.id)
            existing_event_sigs = {
                (
                    ev.event_type,
                    ev.summary,
                )
                for ev in existing_events
            }

            for ev in info["events"]:
                ev.update(
                    {
                        "page_id": target_page.id,
                        "author_type": "agent",
                        "author_id": agent.id,
                    }
                )
                ev_date = ev.get("event_date")
                if isinstance(ev_date, str):
                    try:
                        ev_date = datetime.fromisoformat(ev_date)
                    except ValueError:
                        ev_date = None
                if not ev_date:
                    ev_date = datetime.combine(
                        today_dt, datetime.min.time(), tzinfo=timezone.utc
                    )
                ev["event_date"] = ev_date
                rel_names = ev.get("related_pages", [])
                rel_ids: List[int] = []
                for nm in rel_names:
                    tp = all_name_to_page.get(normalize_name(nm))
                    if tp:
                        rel_ids.append(tp.id)
                ev["related_page_ids"] = rel_ids
                ev.pop("related_pages", None)
                sig = (
                    ev.get("event_type"),
                    ev.get("summary"),
                )
                if sig in existing_event_sigs:
                    continue
                await crud_page.create_key_event(session, PageKeyEvent(**ev))
                existing_event_sigs.add(sig)

            seen_targets: set[int] = set()
            for rel in info["rels"]:
                tname = rel.get("target_page")
                tpage = all_name_to_page.get(normalize_name(tname)) if tname else None
                if not tpage or tpage.id in seen_targets:
                    continue
                seen_targets.add(tpage.id)
                await remove_existing_relationships(target_page.id, tpage.id)
                rel_obj = PageRelationship(
                    page_id=target_page.id,
                    target_page_id=tpage.id,
                    relationship_type=rel.get("relationship_type"),
                    direction=rel.get("direction", "outgoing"),
                    source_page_id=rel.get("source_page_id"),
                    description=rel.get("description"),
                    author_type="agent",
                    author_id=agent.id,
                )
                await crud_page.create_relationship(session, rel_obj)

            updated.append({"name": target_page.name, "id": target_page.id})

    embedding_jobs: list[dict] = []
    embeddings = await crud_world_embedding.get_embeddings(session, page.gameworld_id)
    affected_ids = [p["id"] for p in results] + [p["id"] for p in updated]

    if embeddings and affected_ids:
        for emb in embeddings:
            for pid in affected_ids:
                crud_vectordb.delete_page(emb.id, pid)
            await asyncio.gather(
                *(crud_vectordb.add_page(session, pid, emb.id) for pid in affected_ids)
            )
            result = await session.execute(
                select(func.count(Page.id)).where(Page.gameworld_id == emb.world_id)
            )
            total_pages = result.scalar_one()
            await crud_world_embedding.update_embedding(
                session,
                emb.id,
                {
                    "last_index_time": datetime.now(timezone.utc),
                    "page_count": total_pages,
                },
            )

    return {"pages": results, "updated": updated, "embedding_jobs": embedding_jobs}


async def generate_pages(
    session: AsyncSession,
    agent: Agent,
    page: Page,
    page_specs: List[dict],
    merge_groups: Optional[List[List[str]]] = None,
) -> dict:
    """Orchestrate generation or update of pages from specs.

    Parameters
    ----------
    session: AsyncSession
        Database session.
    agent: Agent
        Agent performing the generation.
    page: Page
        Target page for generation.
    page_specs: List[dict]
        Specifications for pages to create or update.
    merge_groups: Optional[List[List[str]]]
        Groups of page names to treat as aliases during generation.
    """

    if page.gameworld_id != agent.world_id:
        raise ValueError("Agent and page belong to different worlds")
    return await generate_pages_worker(session, agent, page, page_specs, merge_groups)
