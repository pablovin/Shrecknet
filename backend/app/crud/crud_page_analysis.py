from sqlalchemy.ext.asyncio import AsyncSession
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from typing import Dict, List
from difflib import SequenceMatcher
import unicodedata

from datetime import datetime, timezone

from app.config import settings
from app.models.model_page import Page, PageKeyEvent, PageRelationship
from app.models.model_agent import Agent
from app.models.model_concept import Concept
from app.crud import crud_page, crud_concept, crud_vectordb
from app.crud import crud_characteristic, crud_agent_embedding
from app.crud.crud_agent import ensure_personality_prompts
import json
from bs4 import BeautifulSoup
from rapidfuzz import fuzz, process

import asyncio
from bs4 import BeautifulSoup

def strip_html(text):
    soup = BeautifulSoup(text or "", "html.parser")
    return soup.get_text(separator=" ", strip=True)

def split_html_by_headers(html, header_tags=("h1", "h2", "h3")):
    soup = BeautifulSoup(html, "html.parser")
    headers = []
    for tag in header_tags:
        headers += soup.find_all(tag)
    headers = sorted(headers, key=lambda x: x.sourceline if hasattr(x, 'sourceline') and x.sourceline else 0)

    chunks = []
    for i, h in enumerate(headers):
        section_texts = [h.get_text(separator=' ', strip=True)]
        for sib in h.next_siblings:
            if getattr(sib, 'name', None) in header_tags:
                break
            if getattr(sib, 'get_text', None):
                txt = sib.get_text(separator=' ', strip=True)
                if txt:
                    section_texts.append(txt)
            elif isinstance(sib, str):
                stripped = sib.strip()
                if stripped:
                    section_texts.append(stripped)
        chunk = "\n".join(section_texts).strip()
        if chunk:
            chunks.append(chunk)
    return chunks


  
async def _get_agent_embeddings(session: AsyncSession, agent: Agent):
    """Return valid world embeddings associated with the agent."""
    embeddings = await crud_agent_embedding.get_embeddings(session, agent.id)
    return [e for e in embeddings if e.last_index_time]


async def _query_agent_world(
    session: AsyncSession,
    agent: Agent,
    query: str,
    n_results: int = 5,
    views: List[str] | None = None,
    filters: Dict | None = None,
    max_chunks_per_page: int | None = 15,
):
    chunks = []
    embeddings = await _get_agent_embeddings(session, agent)
    for emb in embeddings:
        try:
            parts = crud_vectordb.query_world(
                emb.id,
                query,
                n_results=n_results,
                views=views,
                filters=filters,
                max_chunks_per_page=max_chunks_per_page,
            )
            chunks.extend(parts)
        except Exception:
            continue
    return chunks[:n_results]


def normalize_name(name):
    # Remove accents, lowercase, trim spaces
    name = unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode('ASCII')
    name = name.lower().strip()
    # Remove leading 'o ', 'a ', 'os ', 'as ', etc.
    for prefix in ["o ", "a ", "os ", "as ", "barão ", "lady ", "rei ", "rainha "]:
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name




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


def find_best_page_match(name, existing_titles, threshold=80):
    name_norm = normalize_name(name)
    choices = [normalize_name(t) for t in existing_titles.keys()]
    match, score, idx = process.extractOne(name_norm, choices, scorer=fuzz.token_set_ratio)
    if score >= threshold:
        matched_key = list(existing_titles.keys())[idx]  # get the original key
        return matched_key
    return None



def split_html_by_headers(html, header_tags=("h1", "h2", "h3")):
    soup = BeautifulSoup(html, "html.parser")
    # Find all headers in order
    headers = []
    for tag in header_tags:
        headers += soup.find_all(tag)
    headers = sorted(headers, key=lambda x: x.sourceline if hasattr(x, 'sourceline') and x.sourceline else 0)

    chunks = []
    for i, h in enumerate(headers):
        # Use space separator!
        section_texts = [h.get_text(separator=' ', strip=True)]
        for sib in h.next_siblings:
            if getattr(sib, 'name', None) in header_tags:
                break
            if getattr(sib, 'get_text', None):
                txt = sib.get_text(separator=' ', strip=True)
                if txt:
                    section_texts.append(txt)
            elif isinstance(sib, str):
                stripped = sib.strip()
                if stripped:
                    section_texts.append(stripped)
        chunk = "\n".join(section_texts).strip()
        if chunk:
            chunks.append(chunk)
    return chunks

def ensure_visible_text(chunk):
    # If already plain text, return as is
    if isinstance(chunk, str):
        return chunk.strip()
    # If Document object, get .page_content
    text = getattr(chunk, "page_content", str(chunk))
    # Strip all HTML tags, merge inline elements
    return BeautifulSoup(text, "html.parser").get_text(separator='', strip=True)

async def get_page_view_chunks(session, agent, page):
    """
    Returns a dict: {"narrative": ..., "relationship": ..., "event": ...}
    Each value is the concatenated plain text for that view from embeddings.
    """
    valid_embeds = await _get_agent_embeddings(session, agent)
    if not valid_embeds:
        return {}
    views = ["relationship", "event"]
    view_chunks = {}

    for emb in valid_embeds:
        for view in views:
            parts = crud_vectordb.get_page_chunks(emb.id, page.id, view=view)
            if parts:
                # Accepts both list-of-strings and list-of-objects with .page_content
                texts = [ensure_visible_text(p) for p in parts if p]
                if texts:
                    view_chunks[view] = "\n".join(texts)
        if len(view_chunks) == len(views):
            break  # Got all
    return view_chunks

async def analyze_page(session, agent, page) -> dict:
    # --- 1. Load concepts, pages, agent embeddings ---
    valid_embeds = await _get_agent_embeddings(session, agent)
    if not valid_embeds:
        return {"suggestions": [], "error": "Agent world embeddings missing"}

    concepts = await crud_concept.get_concepts(session, gameworld_id=page.gameworld_id, auto_generated=True)
    concept_defs = {c.name: c.description or "" for c in concepts}
    concepts_by_id = {c.id: c for c in concepts}
    concepts_by_name = {normalize_name(c.name): c for c in concepts}
    existing_pages = await crud_page.get_pages(session, gameworld_id=page.gameworld_id)
    existing_titles_norm = {normalize_name(p.name): p for p in existing_pages if p.name}

    # --- 2. Get global context (views) from page embeddings ---
    view_chunks = await get_page_view_chunks(session, agent, page)
    metadata_only_text = "\n\n".join(
        f"{view.title()}:\n{content.strip()}"
        for view, content in view_chunks.items()
        if content and content.strip()
    )

    # --- 3. Prepare LLM chain ---
    prompt = ChatPromptTemplate.from_messages([
        ("system",
            "You are analyzing a story. Extract important concepts and suggest the canonical page names for each, along with the concept (category/type) each belongs to, using the most complete and unambiguous name possible."
            "\nIf possible, map aliases and short references to their main page name."
            "\nConcept list:\n"
            + "\n".join(f"- {name}: {desc}" for name, desc in concept_defs.items())
            + "\nReturn a JSON object mapping each page name to its concept (category), e.g.:\n"
            + "{\n  \"Barão de Karst\": \"NPC\",\n  \"Abelardo\": \"NPC\",\n  \"Yudennach\": \"Reino\",\n  \"Green Dragon\": \"Monstro\"\n}"
            + "\nDo not include mentions that are not mapped to a concept. Only return the JSON object."
        ),
        ("user", "{chunk}")
    ])
    llm = ChatOpenAI(api_key=settings.openai_api_key, model=settings.open_ai_model)
    chain = prompt | llm

    # --- 4. Metadata-only pass ---
    all_pairs = []
    if metadata_only_text.strip():
        try:
            result = await chain.ainvoke({"chunk": metadata_only_text})
            parsed = json.loads(result.content.strip())
            if isinstance(parsed, dict):
                all_pairs.extend(parsed.items())
        except Exception as e:
            print(f"Metadata pass failed: {e}")

    # --- 5. Chunked content pass (header-based) ---
    page_chunks = split_html_by_headers(page.content or "")
    chunk_texts = [ensure_visible_text(chunk) for chunk in page_chunks if chunk and chunk.strip()]

    async def process_chunk(chunk_text):
        # Prepend metadata to each chunk
        chunk_input = f"{metadata_only_text}\n\n{chunk_text}".strip() if metadata_only_text else chunk_text
        try:
            result = await chain.ainvoke({"chunk": chunk_input})
            parsed = json.loads(result.content.strip())
            if isinstance(parsed, dict):
                return parsed.items()
        except Exception as e:
            print(f"Chunk LLM failed: {e}")
        return []

    if chunk_texts:
        chunk_results = await asyncio.gather(*(process_chunk(chunk) for chunk in chunk_texts))
        for res in chunk_results:
            all_pairs.extend(res)

    # --- 6. Merge and deduplicate results ---
    groups = {}
    for name, concept_name in all_pairs:
        norm = normalize_name(name)
        if norm not in groups:
            groups[norm] = {"names": [], "concepts": set()}
        groups[norm]["names"].append(name)
        groups[norm]["concepts"].add(concept_name)

    suggestions = []
    already_handled = set()
    for norm, data in groups.items():
        page_obj = existing_titles_norm.get(norm)
        all_names = data["names"]
        all_concepts = list(data["concepts"])
        exists = bool(page_obj)

        if exists:
            # Use actual page name, and DB concept if possible
            best_name = page_obj.name
            concept_obj = concepts_by_id.get(page_obj.concept_id) if getattr(page_obj, "concept_id", None) else None
            final_concept_id = concept_obj.id if concept_obj else None
            final_concept_name = concept_obj.name if concept_obj else (all_concepts[0] if all_concepts else "Unknown")
            if page_obj.id in already_handled:
                continue
            already_handled.add(page_obj.id)
        else:
            # For new pages, use *shortest* suggested name (most likely to be canonical)
            best_name = min(all_names, key=len)
            llm_concept = all_concepts[0] if all_concepts else "Unknown"
            concept_obj = concepts_by_name.get(normalize_name(llm_concept))
            final_concept_id = concept_obj.id if concept_obj else None
            final_concept_name = concept_obj.name if concept_obj else llm_concept

        suggestions.append({
            "name": best_name,
            "concept_id": final_concept_id,
            "concept": final_concept_name,
            "mode": "update" if exists else "create",
            "exists": exists,
            "target_page_id": page_obj.id if page_obj else None,
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


async def generate_pages(
    session: AsyncSession,
    agent: Agent,
    page: Page,
    page_specs: List[dict],
) -> dict:
    """Create new pages based on suggestions.

    The function now processes suggestions grouped by their source pages so that
    each chunk of a source page is analysed only once.  For every chunk we ask
    the language model to produce the ``autogenerated_content`` as well as any
    ``PageKeyEvent`` and ``PageRelationship`` for **all** pages associated with
    that source.
    """

    # Filter only brand new pages
    create_specs = [s for s in page_specs if s.get("mode") == "create"]
    if not create_specs:
        return {"pages": []}

    concept_ids = {s["concept_id"] for s in create_specs}
    concepts = await crud_concept.get_concepts(session)
    concept_map = {c.id: c for c in concepts if c.id in concept_ids}

    all_pages = await crud_page.get_pages(session)
    page_map = {p.id: p for p in all_pages}

    # Map source page -> specs referencing it
    sources_map: Dict[int, List[dict]] = {}
    for spec in create_specs:
        for pid in spec.get("source_page_ids", []):
            if pid in page_map:
                sources_map.setdefault(pid, []).append(spec)

    if not sources_map:
        return {"pages": []}

    llm = ChatOpenAI(api_key=settings.openai_api_key, model=settings.open_ai_model)

    # Storage for intermediate results
    spec_data: Dict[int, Dict[str, list]] = {
        id(spec): {"parts": [], "events": [], "rels": [], "spec": spec}
        for spec in create_specs
    }

    async def process_chunk(sp: Page, chunk: str, specs: List[dict]):
        """Ask the LLM for all pages in ``specs`` for this chunk."""
        pages_desc = "\n".join(
            f"{s['name']}: {concept_map[s['concept_id']].auto_generated_prompt or ''}"
            for s in specs
        )
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "Extract information for the pages listed below. "
                    "Respond with JSON mapping each page name to its generated "
                    "content, key_events and relationships.",
                ),
                ("user", "Pages:\n{pages}\n\nText:\n{text}"),
            ]
        )
        chain = prompt | llm
        resp = await chain.ainvoke({"pages": pages_desc, "text": chunk})
        try:
            payload = json.loads(resp.content)
        except Exception:
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

    await asyncio.gather(*(process_source(pid, specs) for pid, specs in sources_map.items()))

    results = []
    for sid, info in spec_data.items():
        spec = info["spec"]
        concept = concept_map.get(spec["concept_id"])
        if not concept:
            continue
        gameworld_id = page_map[spec.get("source_page_ids", [page.id])[0]].gameworld_id
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

        # Deduplicate events and relationships
        unique_events = {json.dumps(ev, sort_keys=True) for ev in info["events"]}
        unique_rels = {json.dumps(rel, sort_keys=True) for rel in info["rels"]}

        for ev_json in unique_events:
            ev = json.loads(ev_json)
            ev.update({"page_id": new_page.id, "author_type": "agent", "author_id": agent.id})
            await crud_page.create_key_event(session, PageKeyEvent(**ev))

        for rel_json in unique_rels:
            rel = json.loads(rel_json)
            rel.update({"page_id": new_page.id, "author_type": "agent", "author_id": agent.id})
            await crud_page.create_relationship(session, PageRelationship(**rel))

        results.append({"name": new_page.name, "id": new_page.id})

    return {"pages": results}




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
