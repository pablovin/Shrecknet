from __future__ import annotations

import asyncio
import json
from difflib import SequenceMatcher
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List
from uuid import uuid4

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
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
    split_html_by_headers,
)
from app.crud import crud_concept, crud_page, crud_world_embedding
from app.models.model_agent import Agent
from app.models.model_page import Page, PageKeyEvent, PageRelationship
from app.task_queue import task_rebuild_world_embedding


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
    session: AsyncSession, agent: Agent, page: Page, page_specs: List[dict]
) -> dict:
    """Generate or update pages based on specifications."""

    create_specs = [s for s in page_specs if s.get("mode", "create") == "create"]
    update_specs = [s for s in page_specs if s.get("mode") == "update"]
    if not create_specs and not update_specs:
        return {"pages": [], "updated": []}

    concept_ids = {s["concept_id"] for s in page_specs}
    concepts = await crud_concept.get_concepts(session)
    concept_map = {c.id: c for c in concepts if c.id in concept_ids}

    all_pages = await crud_page.get_pages(session)
    page_map = {p.id: p for p in all_pages}
    name_to_page = {normalize_name(p.name): p for p in all_pages}

    sources_map: Dict[int, List[dict]] = {}
    for spec in create_specs:
        for pid in spec.get("source_page_ids", []):
            if pid in page_map:
                sources_map.setdefault(pid, []).append(spec)

    if create_specs and not sources_map:
        return {"pages": [], "updated": []}

    llm = ChatOpenAI(api_key=settings.openai_api_key, model=settings.open_ai_model)

    spec_data: Dict[int, Dict[str, list]] = {
        id(spec): {"parts": [], "events": [], "rels": [], "spec": spec}
        for spec in create_specs
    }

    if create_specs:
        await asyncio.gather(
            *(
                generate_process_source_worker(
                    page_map[pid], specs, llm, concept_map, spec_data
                )
                for pid, specs in sources_map.items()
            )
        )

    created_pages = []
    new_name_to_page = {}
    if create_specs:
        for sid, info in spec_data.items():
            spec = info["spec"]
            concept = concept_map.get(spec["concept_id"])
            if not concept:
                continue
            source_ids = spec.get("source_page_ids", [page.id])
            gameworld_id = (
                page_map[source_ids[0]].gameworld_id
                if source_ids
                else page.gameworld_id
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

    today_dt = datetime.now(timezone.utc)
    results = []
    updated = []

    for sid, info in spec_data.items():
        spec = info["spec"]
        page_obj = new_name_to_page.get(normalize_name(spec["name"]))
        if not page_obj:
            continue

        unique_events = {json.dumps(ev, sort_keys=True) for ev in info["events"]}
        unique_rels = {json.dumps(rel, sort_keys=True) for rel in info["rels"]}

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
                ev_date = today_dt
            ev["event_date"] = ev_date
            related_names = ev.get("related_pages", [])
            related_ids = []
            for name in related_names:
                target_page = all_name_to_page.get(normalize_name(name))
                if target_page:
                    related_ids.append(target_page.id)
            ev["related_page_ids"] = related_ids
            ev.pop("related_pages", None)

            await crud_page.create_key_event(session, PageKeyEvent(**ev))

        for rel_json in unique_rels:
            rel = json.loads(rel_json)
            rel.update(
                {
                    "page_id": page_obj.id,
                    "author_type": "agent",
                    "author_id": agent.id,
                }
            )
            target_name = rel.get("target_page")
            target_page = (
                all_name_to_page.get(normalize_name(target_name))
                if target_name
                else None
            )
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
        for spec in update_specs:
            target_page = page_map.get(spec.get("target_page_id"))
            concept = concept_map.get(spec.get("concept_id"))
            if not target_page or not concept:
                continue
            texts = []
            if target_page.autogenerated_content:
                texts.append(strip_html(target_page.autogenerated_content))
            elif target_page.content:
                texts.append(strip_html(target_page.content))
            for pid in spec.get("source_page_ids", []) or []:
                sp = page_map.get(pid)
                if sp and sp.content:
                    texts.extend(split_html_by_headers(sp.content))
            if not texts:
                continue

            instructions = (
                concept.auto_generated_prompt
                or "(no instructions, just summarize relevant content for this page)"
            )
            sys_prompt = (
                "You are an expert RPG campaign chronicler.\n"
                "Using the existing page notes and the additional text, update the page.\n"
                "Return valid JSON with keys 'autogenerated_content', 'key_events', and 'relationships'."
            )
            prompt = ChatPromptTemplate.from_messages(
                [
                    ("system", f"{sys_prompt}\nInstructions: {instructions}"),
                    ("user", "{text}"),
                ]
            )
            chain = prompt | update_llm
            try:
                resp = await chain.ainvoke({"text": "\n\n".join(texts)})
                payload = json.loads(resp.content)
            except Exception as e:
                print(f"[update_page] LLM/JSON error: {e}")
                continue

            new_text = payload.get("autogenerated_content", "")
            await crud_page.update_page(
                session,
                target_page.id,
                {"autogenerated_content": new_text, "updated_by_agent_id": agent.id},
            )

            existing_rels = await crud_page.get_relationships(session, target_page.id)
            rel_pairs = {(r.target_page_id, r.direction) for r in existing_rels}
            rel_type_map = {
                r.target_page_id: r.relationship_type for r in existing_rels
            }

            existing_events = await crud_page.get_key_events(session, target_page.id)
            existing_event_types = {ev.event_type for ev in existing_events}

            today_dt = datetime.now(timezone.utc)

            for ev in payload.get("key_events", []) or []:
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
                    ev_date = today_dt
                ev["event_date"] = ev_date
                rel_names = ev.get("related_pages", [])
                rel_ids = []
                for nm in rel_names:
                    tp = all_name_to_page.get(normalize_name(nm))
                    if tp:
                        rel_ids.append(tp.id)
                ev["related_page_ids"] = rel_ids
                ev.pop("related_pages", None)
                if ev.get("event_type") in existing_event_types:
                    pass
                await crud_page.create_key_event(session, PageKeyEvent(**ev))

        for rel in payload.get("relationships", []) or []:
            tname = rel.get("target_page")
            tpage = all_name_to_page.get(normalize_name(tname)) if tname else None
            if not tpage:
                continue
            direction = rel.get("direction", "outgoing")
            pair = (tpage.id, direction)
            if pair in rel_pairs:
                continue
            rtype = rel.get("relationship_type")
            if tpage.id in rel_type_map:
                rtype = rel_type_map[tpage.id]
            rel_obj = PageRelationship(
                page_id=target_page.id,
                target_page_id=tpage.id,
                relationship_type=rtype,
                direction=direction,
                source_page_id=None,
                description=rel.get("description"),
                author_type="agent",
                author_id=agent.id,
            )
            await crud_page.create_relationship(session, rel_obj)

        updated.append({"name": target_page.name, "id": target_page.id})

    embedding_jobs: list[dict] = []
    embeddings = await crud_world_embedding.get_embeddings(session, page.gameworld_id)
    if embeddings:
        job_dir = Path(settings.world_embedding_job_dir)
        job_dir.mkdir(parents=True, exist_ok=True)
        for emb in embeddings:
            job_id = uuid4().hex
            job_path = job_dir / f"{job_id}.json"
            with open(job_path, "w") as f:
                json.dump(
                    {
                        "status": "queued",
                        "embedding_id": emb.id,
                        "job_type": "rebuild_world_embedding",
                    },
                    f,
                )
            task_rebuild_world_embedding.delay(emb.id, job_id)
            embedding_jobs.append({"embedding_id": emb.id, "job_id": job_id})

    return {"pages": results, "updated": updated, "embedding_jobs": embedding_jobs}


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
