from sqlalchemy.ext.asyncio import AsyncSession
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from typing import Dict, List
from difflib import SequenceMatcher
import unicodedata
from datetime import datetime, timezone

from app.config import settings
from app.models.model_page import Page
from app.models.model_agent import Agent
from app.models.model_concept import Concept
from app.crud import crud_page, crud_concept, crud_vectordb
from app.crud import crud_characteristic
from app.crud.crud_agent import ensure_personality_prompts
import json
from langchain.text_splitter import RecursiveCharacterTextSplitter


def _valid_name(name: str) -> bool:
    if not name:
        return False
    n = name.lower()
    invalid = ["not explicitly", "not mentioned", "no extra", "não há menção", "nao ha mencao", "none", "no unique", "mencionado", "no texto"]
    for term in invalid:
        if term in n:
            return False
    return True


def _post_process_names(names: set[str]) -> set[str]:
    """Additional heuristics to remove unlikely concept names."""
    processed: set[str] = set()
    for name in names:
        if not _valid_name(name):
            continue
        if len(name.split()) > 8:
            continue
        processed.add(name.strip())
    return processed


def _normalize(text: str) -> str:
    """Return a lowercase version of the text without diacritics and extra spaces."""
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join(text.lower().split())


def _canonical(name: str) -> str:
    """Return a normalized version of the name without honorifics."""
    n = _normalize(name)
    titles = {"lord", "lady", "sir", "dame", "mr", "mrs", "ms"}
    words = [w for w in n.split() if w not in titles]
    return " ".join(words)


def _select_key(name: str, groups: Dict[str, List[dict]]) -> str:
    """Return the canonical key for grouping similar names."""
    canonical = _canonical(name)
    for k in list(groups.keys()):
        if k.startswith(canonical):
            groups[canonical] = groups.pop(k)
            return canonical
        if canonical.startswith(k):
            return k
    return canonical


def _find_existing_page(name: str, page_map: Dict[str, int]) -> int | None:
    """Find existing page id by canonical or fuzzy match."""
    key = _canonical(name)
    if key in page_map:
        return page_map[key]
    for k, pid in page_map.items():
        if key.startswith(k) or k.startswith(key):
            return pid
        if SequenceMatcher(None, key, k).ratio() > 0.85:
            return pid
    return None


_text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)


async def analyze_page(session, agent: Agent, page: Page) -> dict:
    # Step 1: Load concepts and existing pages
    concepts = await crud_concept.get_concepts(session, gameworld_id=page.gameworld_id, auto_generated=True)
    concept_defs = {c.name: c.description or "" for c in concepts}
    concept_names = list(concept_defs.keys())
    existing_pages = await crud_page.get_pages(session, gameworld_id=page.gameworld_id)
    existing_titles = {p.name.lower(): p.id for p in existing_pages if p.name}

    # Step 2: Chunk the page into logical pieces
    content = page.content or ""
    text_chunks = _text_splitter.split_text(content)

    # Step 3: Retrieve surrounding context to guide LLM extraction
    retrieved_concepts = set()
    for chunk in text_chunks:
        related_chunks = crud_vectordb.query_world(
            page.gameworld_id,
            chunk,
            n_results=8,
            views=["relationship", "event", "narrative"]
        )
        for c in related_chunks:
            if "concept_name" in c:
                retrieved_concepts.add(c["concept_name"])

    # Filter retrieved concepts to only those that are auto_generated
    filtered_retrieved_concepts = [c for c in retrieved_concepts if c in concept_defs]

    # Step 4: Run LLM extraction per chunk using relevant concepts only
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are analyzing a story. Extract ONLY important concept mentions that match existing known concepts.\n"
                   + "Concept list:\n"
                   + "\n".join(f"- {name}: {desc}" for name, desc in concept_defs.items() if name in filtered_retrieved_concepts)
                   + "\nReturn a JSON object like: { \"<name>\": \"<concept_type>\" }"),
        ("user", "{chunk}")
    ])
    llm = ChatOpenAI(api_key=settings.openai_api_key, model=settings.open_ai_model)
    chain = prompt | llm

    found_names: Dict[str, str] = {}
    for chunk in text_chunks:
        try:
            result = await chain.ainvoke({"chunk": chunk})
            parsed = json.loads(result.content.strip())
            found_names.update(parsed)
        except Exception:
            continue

    # Step 5: Normalize and match to existing pages
    suggestions = []
    for name, concept_name in found_names.items():
        concept_obj = next((c for c in concepts if c.name == concept_name), None)
        if not concept_obj:
            continue

        page_id = existing_titles.get(name.lower())
        suggestions.append({
            "name": name,
            "concept_id": concept_obj.id,
            "concept": concept_name,
            "mode": "update" if page_id else "create",
            "exists": bool(page_id),
            "target_page_id": page_id,
            "source_pages": [{"id": page.id, "name": page.name}],
            "source_page_ids": [page.id],
            "source_page_updated": (page.updated_at.isoformat() if page.updated_at else "")
        })

    return {"suggestions": suggestions}


async def _choose_concept(llm: ChatOpenAI, name: str, content: str, options: List[Concept]) -> str:
    prompt = ChatPromptTemplate.from_messages([
        ("system", "Choose the most appropriate concept for the given name. Respond with the concept name only."),
        (
            "user",
            "Name: {name}\nOptions: {opts}\nExcerpt: {text}\nAnswer with only one concept name.",
        ),
    ])
    opts = "; ".join([f"{c.name}: {c.description or ''}" for c in options])
    chain = prompt | llm
    resp = await chain.ainvoke({"name": name, "opts": opts, "text": content[:1000]})
    return resp.content.strip()


async def generate_pages_from_suggestions(session, agent: Agent, suggestions: List[dict]) -> List[dict]:
    # Batch preload everything needed
    concept_ids = {s["concept_id"] for s in suggestions}
    concepts = await crud_concept.get_concepts(session, auto_generated=True)
    concept_map = {c.id: c for c in concepts if c.id in concept_ids}

    all_pages = await crud_page.get_pages(session)
    source_pages = {p.id: p for p in all_pages if p.id in all_pages}

    results = []

    for spec in suggestions:
        concept = concept_map.get(spec["concept_id"])
        if not concept:
            continue

        source_ids = spec.get("source_page_ids", [])
        sources = [source_pages[pid] for pid in source_ids if pid in source_pages]
        if not sources:
            continue

        prompt = concept.auto_generated_prompt or ""  # Default to blank if none

        # Build source summary
        source_blocks = []
        for sp in sources:
            text = f"---\nSOURCE PAGE: {sp.name}\n\nCONTENT:\n{sp.content}\n\nAUTOGENERATED:\n{sp.autogenerated_content or ''}\n"
            source_blocks.append(text)

        joined_sources = "\n".join(source_blocks)

        prompt_template = ChatPromptTemplate.from_messages([
            ("system", f"You are an intelligent writer agent. Given the concept: '{concept.name}', and the task: '{prompt}', generate an autogenerated content for the page below. Then, extract relevant relationships and events that appear in the text. Respond in JSON with keys: 'autogenerated_content', 'relationships', and 'key_events'."),
            ("user", "{source_text}"),
        ])

        chain = prompt_template | ChatOpenAI(api_key=settings.openai_api_key, model=openai_model)

        try:
            response = await chain.ainvoke({"source_text": joined_sources})
            parsed = json.loads(response.content.strip())
        except Exception:
            continue

        # Build Page suggestion
        page_data = {
            "mode": "create" if not source_ids else "update",
            "name": spec["name"],
            "gameworld_id": sources[0].gameworld_id,
            "concept_id": concept.id,
            "allow_crosslinks": True,
            "ignore_crosslink": False,
            "allow_crossworld": True,
            "updated_by_agent_id": agent.id,
            "autogenerated_content": parsed.get("autogenerated_content", ""),
            "key_events": [],
            "relationships": [],
        }

        # Build relationships
        for r in parsed.get("relationships", []):
            try:
                page_data["relationships"].append({
                    "relationship_type": r["relationship_type"],
                    "target_page_id": r["target_page_id"],
                    "description": r.get("description"),
                    "source_page_id": source_ids[0],
                    "author_type": "agent",
                    "author_id": agent.id,
                })
            except Exception:
                continue

        # Build events
        for e in parsed.get("key_events", []):
            try:
                event_date = e.get("event_date")
                if event_date:
                    event_date = datetime.fromisoformat(event_date)

                page_data["key_events"].append({
                    "event_type": e["event_type"],
                    "event_date": event_date,
                    "summary": e.get("summary"),
                    "related_page_ids": e.get("related_page_ids", []),
                    "source_page_id": source_ids[0],
                    "author_type": "agent",
                    "author_id": agent.id,
                })
            except Exception:
                continue

        results.append(page_data)

    return results




# async def async def generate_pages(session: AsyncSession, agent: Agent, page: Page, page_specs: List[dict]):
#     print (f" -- GENERATING PAGE!")
#     """Generate full page data for selected suggestions.

#     Each page spec may include ``source_page_ids`` which will be used to
#     aggregate the text from those pages before sending it to the language model.
#     """
#     llm = ChatOpenAI(api_key=settings.openai_api_key or "sk-test", model=settings.open_ai_model)
#     generated = []

#     personalities = [p.strip() for p in (agent.personality or "helpful NPC").split(',') if p.strip()]
#     prompts = await ensure_personality_prompts(personalities)
#     tone = "\n".join(prompts.get(p, "") for p in personalities if prompts.get(p))

#     # Preload existing pages in the same world to resolve page_ref values
#     existing_pages = await crud_page.get_pages(session, gameworld_id=page.gameworld_id)

#     def find_page_id(name: str, ref_concept_id: int | None = None) -> int | None:
#         """Lookup for a page by fuzzy name and optional concept."""
#         if not name:
#             return None
#         target = _canonical(name)
#         best_id: int | None = None
#         best_ratio = 0.0
#         for p in existing_pages:
#             if ref_concept_id is not None and p.concept_id != ref_concept_id:
#                 continue
#             candidate = _canonical(p.name)
#             if not candidate:
#                 continue
#             if target in candidate or candidate in target:
#                 return p.id
#             ratio = SequenceMatcher(None, target, candidate).ratio()
#             if ratio > 0.8 and ratio > best_ratio:
#                 best_ratio = ratio
#                 best_id = p.id
#         return best_id

#     for spec in page_specs:
#         concept = await crud_concept.get_concept(session, spec["concept_id"])
#         if not concept:
#             continue
#         characteristics = await crud_characteristic.get_characteristics_for_concept(session, concept.id)
#         char_names = ", ".join(c.name for c in characteristics)
#         prompt = ChatPromptTemplate.from_messages([
#             (
#                 "system",
#                 "You are a skilled writer summarizing fantasy lore. " + tone + " Extract characteristic values found in the text and craft a short, well written narrative recount of the concept's story. Create the text using the same language as the given text. Respond only with valid JSON in the format {{\"autogenerated_content\": <text>, \"values\": {{<characteristic>: [<values>]}}}}. Do not include any other text."
#             ),
#             (
#                 "user",
#                 "Page name: {name}\nConcept: {concept}\nCharacteristics: {chars}\nText:\n{content}"
#             ),
#         ])
#         chain = prompt | llm

#         sources = []
#         if spec.get("source_page_ids"):
#             for pid in spec["source_page_ids"]:
#                 sp = next((pp for pp in existing_pages if pp.id == pid), None)
#                 if sp:
#                     sources.append(sp)
#         else:
#             sources.append(page)

#         sources = [s for s in sources if s and s.content]
#         sources.sort(
#             key=lambda s: (s.updated_at or s.created_at) if (s.updated_at or s.created_at) else datetime.min
#         )

#         sections: List[str] = []
#         value_map: Dict[int, List[str]] = {}

#         for sp in sources:
#             resp = await chain.ainvoke({
#                 "name": spec["name"],
#                 "concept": concept.name,
#                 "chars": char_names,
#                 "content": sp.content,
#             })
#             text = resp.content.strip()
#             try:
#                 data = json.loads(text)
#             except Exception:
#                 data = {"autogenerated_content": text, "values": {}}
#             vals = data.get("values", {}) if isinstance(data.get("values", {}), dict) else {}
#             for c in characteristics:
#                 val = vals.get(c.name)
#                 if not val:
#                     continue
#                 val_list = val if isinstance(val, list) else [val]
#                 if c.type == "page_ref":
#                     refs: List[str] = []
#                     for v in val_list:
#                         pid = find_page_id(str(v), c.ref_concept_id)
#                         if pid is not None:
#                             refs.append(str(pid))
#                     if refs:
#                         value_map.setdefault(c.id, []).extend(refs)
#                 else:
#                     value_map.setdefault(c.id, []).extend([str(v) for v in val_list])

#             date_str = (
#                 (sp.updated_at or sp.created_at or datetime.now(timezone.utc)).date().isoformat()
#             )
#             header = f"<h2>Notes from {sp.name} - {date_str}</h2>"
#             sections.append(header + "\n" + data.get("autogenerated_content", ""))

#         values = [
#             {"characteristic_id": cid, "value": list({*vals})}
#             for cid, vals in value_map.items()
#             if vals
#         ]

#         generated.append({
#             "name": spec["name"],
#             "gameworld_id": page.gameworld_id,
#             "concept_id": concept.id,
#             "allow_crosslinks": True,
#             "ignore_crosslink": False,
#             "allow_crossworld": True,
#             "updated_by_agent_id": agent.id,
#             "autogenerated_content": "\n\n".join(sections),
#             "values": values,
#         })
#         print (f" -- GENERATING PAGE! + {generated}")
#     return {"pages": generated}


    print (f" -- GENERATING PAGE!")
    """Generate full page data for selected suggestions.

    Each page spec may include ``source_page_ids`` which will be used to
    aggregate the text from those pages before sending it to the language model.
    """
    llm = ChatOpenAI(api_key=settings.openai_api_key or "sk-test", model=settings.open_ai_model)
    generated = []

    personalities = [p.strip() for p in (agent.personality or "helpful NPC").split(',') if p.strip()]
    prompts = await ensure_personality_prompts(personalities)
    tone = "\n".join(prompts.get(p, "") for p in personalities if prompts.get(p))

    # Preload existing pages in the same world to resolve page_ref values
    existing_pages = await crud_page.get_pages(session, gameworld_id=page.gameworld_id)

    def find_page_id(name: str, ref_concept_id: int | None = None) -> int | None:
        """Lookup for a page by fuzzy name and optional concept."""
        if not name:
            return None
        target = _canonical(name)
        best_id: int | None = None
        best_ratio = 0.0
        for p in existing_pages:
            if ref_concept_id is not None and p.concept_id != ref_concept_id:
                continue
            candidate = _canonical(p.name)
            if not candidate:
                continue
            if target in candidate or candidate in target:
                return p.id
            ratio = SequenceMatcher(None, target, candidate).ratio()
            if ratio > 0.8 and ratio > best_ratio:
                best_ratio = ratio
                best_id = p.id
        return best_id

    for spec in page_specs:
        concept = await crud_concept.get_concept(session, spec["concept_id"])
        if not concept:
            continue
        characteristics = await crud_characteristic.get_characteristics_for_concept(session, concept.id)
        char_names = ", ".join(c.name for c in characteristics)
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "You are a skilled writer summarizing fantasy lore. " + tone + " Extract characteristic values found in the text and craft a short, well written narrative recount of the concept's story. Create the text using the same language as the given text. Respond only with valid JSON in the format {{\"autogenerated_content\": <text>, \"values\": {{<characteristic>: [<values>]}}}}. Do not include any other text."
            ),
            (
                "user",
                "Page name: {name}\nConcept: {concept}\nCharacteristics: {chars}\nText:\n{content}"
            ),
        ])
        chain = prompt | llm

        sources = []
        if spec.get("source_page_ids"):
            for pid in spec["source_page_ids"]:
                sp = next((pp for pp in existing_pages if pp.id == pid), None)
                if sp:
                    sources.append(sp)
        else:
            sources.append(page)

        sources = [s for s in sources if s and s.content]
        sources.sort(
            key=lambda s: (s.updated_at or s.created_at) if (s.updated_at or s.created_at) else datetime.min
        )

        sections: List[str] = []
        value_map: Dict[int, List[str]] = {}

        for sp in sources:
            resp = await chain.ainvoke({
                "name": spec["name"],
                "concept": concept.name,
                "chars": char_names,
                "content": sp.content,
            })
            text = resp.content.strip()
            try:
                data = json.loads(text)
            except Exception:
                data = {"autogenerated_content": text, "values": {}}
            vals = data.get("values", {}) if isinstance(data.get("values", {}), dict) else {}
            for c in characteristics:
                val = vals.get(c.name)
                if not val:
                    continue
                val_list = val if isinstance(val, list) else [val]
                if c.type == "page_ref":
                    refs: List[str] = []
                    for v in val_list:
                        pid = find_page_id(str(v), c.ref_concept_id)
                        if pid is not None:
                            refs.append(str(pid))
                    if refs:
                        value_map.setdefault(c.id, []).extend(refs)
                else:
                    value_map.setdefault(c.id, []).extend([str(v) for v in val_list])

            date_str = (
                (sp.updated_at or sp.created_at or datetime.now(timezone.utc)).date().isoformat()
            )
            header = f"<h2>Notes from {sp.name} - {date_str}</h2>"
            sections.append(header + "\n" + data.get("autogenerated_content", ""))

        values = [
            {"characteristic_id": cid, "value": list({*vals})}
            for cid, vals in value_map.items()
            if vals
        ]

        generated.append({
            "name": spec["name"],
            "gameworld_id": page.gameworld_id,
            "concept_id": concept.id,
            "allow_crosslinks": True,
            "ignore_crosslink": False,
            "allow_crossworld": True,
            "updated_by_agent_id": agent.id,
            "autogenerated_content": "\n\n".join(sections),
            "values": values,
        })
        print (f" -- GENERATING PAGE! + {generated}")
    return {"pages": generated}


async def analyze_pages_bulk(
    session: AsyncSession, agent: Agent, pages: List[Page]
) -> List[dict]:
    """Analyze multiple pages and merge suggestions by fuzzy page name.

    Instead of discarding similar suggestions, accumulate the pages they were
    found in so the reviewer can see every source."""

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
