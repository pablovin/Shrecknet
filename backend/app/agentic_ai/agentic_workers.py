"""Reusable agentic worker functions for conversational and writing agents."""
import asyncio
import json
from difflib import SequenceMatcher
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Tuple

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crud import crud_vectordb, crud_agent_embedding, crud_concept, crud_page
from app.crud.crud_page_analysis import (
    analyze_page,
    normalize_name,
    split_html_by_headers,
    strip_html,
)
from app.models.model_page import Page, PageKeyEvent, PageRelationship
from app.models.model_agent import Agent

openai_model = settings.open_ai_model


async def decompose_question(query: str, max_questions: int = 8) -> List[str]:
    """Break a user query into sub-questions using an LLM."""
    llm = ChatOpenAI(api_key=settings.openai_api_key, model=openai_model)
    prompt = (
        "Given the user's question below, break it down into a list of focused research questions or information needs "
        f"that would help answer it. Limit to at most {max_questions} entries, prefer fewer if possible. "
        "Respond only with a JSON list of strings.\n\n"
        f"User question: {query}\n"
    )
    try:
        resp = await llm.ainvoke(prompt)
        sub_questions = json.loads(resp.content.strip())
        if not isinstance(sub_questions, list):
            sub_questions = [query]
        if not sub_questions:
            sub_questions = [query]
    except Exception:
        sub_questions = [query]
    return sub_questions


async def async_query_all_embeddings(
    session, agent, query: str, n_results: int, views: Iterable[str], max_chunks_per_page: int
):
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
    results = []
    for res_list in all_results:
        for res in res_list:
            res["_from_collection"] = True
            results.append(res)
    return results


async def query_world_embeddings(
    session,
    agent,
    sub_questions: List[str],
    n_results: int,
    views: Iterable[str],
    max_chunks_per_page: int,
):
    """Run vector searches for a list of questions and annotate the results."""
    tasks = [
        async_query_all_embeddings(session, agent, q, n_results * 2, views, max_chunks_per_page)
        for q in sub_questions
    ]
    results_lists = await asyncio.gather(*tasks)
    annotated: List[dict] = []
    for q, res_list in zip(sub_questions, results_lists):
        for r in res_list:
            r["_from_subquestion"] = q
            annotated.append(r)
    return annotated


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
            r["highlights"][0]["score"] if r.get("highlights") and r["highlights"][0].get("score") is not None else 0,
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


def make_validator_prompt(query: str, answer: str, user_nickname: str | None, tone: str) -> str:
    checks = [
        "1. Does the answer fully address the user's question?",
        "2. Does the answer address the user directly" + (f" as '{user_nickname}'" if user_nickname else "") + "?",
        "3. Does the answer maintain the agent's tone/personality? (" + (tone or "No special tone") + ")",
    ]
    prompt = (
        f"User question: {query}\n"
        f"Proposed answer: {answer}\n"
        "Evaluate the answer based on the following criteria:\n" + "\n".join(checks) +
        "\nFor each point, respond with 'yes' or 'no', then summarize briefly in 2-3 sentences."
    )
    return prompt


async def validate_response(query: str, answer: str, user_nickname: str | None, tone: str) -> bool:
    """Return True if the answer passes validation."""
    llm = ChatOpenAI(api_key=settings.openai_api_key, model=openai_model)
    validator_prompt = make_validator_prompt(query, answer, user_nickname, tone)
    resp = await llm.ainvoke(validator_prompt)
    return "no" not in resp.content.lower()


async def analyze_pages_worker(
    session: AsyncSession, agent: Agent, pages: List[Page]
) -> List[dict]:
    """Analyze multiple pages and merge suggestions by fuzzy page name."""
    pages = sorted(
        pages,
        key=lambda p: (p.updated_at or p.created_at)
        if (p.updated_at or p.created_at)
        else datetime.min,
    )

    all_suggestions: List[dict] = []
    for page in pages:
        result = await analyze_page(session, agent, page)
        for s in result.get("suggestions", []):
            entry = dict(s)
            entry["source_pages"] = [{"id": page.id, "name": page.name}]
            entry["source_page_ids"] = [page.id]
            entry["source_page_updated"] = (
                page.updated_at.isoformat() if page.updated_at else ""
            )
            all_suggestions.append(entry)

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

    async def process_chunk(sp: Page, chunk: str, specs: List[dict]):
        pages_instructions = "\n".join(
            f"Page: {s['name']}\nInstructions: {concept_map[s['concept_id']].auto_generated_prompt or '(no instructions, just summarize relevant content for this page)'}"
            for s in specs
        )
        system_prompt = (
            "You are an expert RPG campaign chronicler.\n"
            "For EACH page below, use the provided instructions to generate:\n"
            "1. 'autogenerated_content': follow the instructions for this page and summarize/generate relevant content from the text chunk.\n"
            "2. 'key_events': a list of events for this page, if any, with event_type, event_date (if available), summary, and related_pages (by name, if any, from the text).\n"
            "3. 'relationships': a list of relationships, if any, with relationship_type, target_page (name from the text), direction (default 'outgoing'), and description.\n"
            "If no events or relationships are present, return empty lists.\n"
            "Write everything on the same language as the chunks!\n"
            "Your output MUST be valid JSON, mapping page names to their extracted data.\n\n"
            f"{pages_instructions}\n"
        )
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("user", "Text chunk:\n{text}"),
        ])
        chain = prompt | llm
        try:
            resp = await chain.ainvoke({"text": chunk})
            payload = json.loads(resp.content)
        except Exception as e:
            print(f"[process_chunk] LLM/JSON error: {e}")
            return

        for spec in specs:
            data = payload.get(spec["name"], {}) if isinstance(payload, dict) else {}
            entry = spec_data[id(spec)]
            if data.get("autogenerated_content"):
                entry["parts"].append(data["autogenerated_content"])
            for ev in data.get("key_events", []) or []:
                ev["source_page_id"] = sp.id
                entry["events"].append(ev)
            for rel in data.get("relationships", []) or []:
                rel["source_page_id"] = sp.id
                entry["rels"].append(rel)

    async def process_source(pid: int, specs: List[dict]):
        sp = page_map[pid]
        chunks = split_html_by_headers(sp.content or "")
        await asyncio.gather(*(process_chunk(sp, c, specs) for c in chunks))

    if create_specs:
        await asyncio.gather(
            *(process_source(pid, specs) for pid, specs in sources_map.items())
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
            ev.update({
                "page_id": page_obj.id,
                "author_type": "agent",
                "author_id": agent.id,
            })
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
            rel.update({
                "page_id": page_obj.id,
                "author_type": "agent",
                "author_id": agent.id,
            })
            target_name = rel.get("target_page")
            target_page = (
                all_name_to_page.get(normalize_name(target_name)) if target_name else None
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
        update_llm = ChatOpenAI(api_key=settings.openai_api_key, model=settings.open_ai_model)
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
            prompt = ChatPromptTemplate.from_messages([
                ("system", f"{sys_prompt}\nInstructions: {instructions}"),
                ("user", "{text}"),
            ])
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
            rel_type_map = {r.target_page_id: r.relationship_type for r in existing_rels}

            existing_events = await crud_page.get_key_events(session, target_page.id)
            existing_event_types = {ev.event_type for ev in existing_events}

            today_dt = datetime.now(timezone.utc)

            for ev in payload.get("key_events", []) or []:
                ev.update({
                    "page_id": target_page.id,
                    "author_type": "agent",
                    "author_id": agent.id,
                })
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

    return {"pages": results, "updated": updated}
