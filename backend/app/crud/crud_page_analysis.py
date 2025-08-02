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

from langchain_text_splitters import RecursiveCharacterTextSplitter

def strip_html(text):
    soup = BeautifulSoup(text or "", "html.parser")
    return soup.get_text(separator=" ", strip=True)

  
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


# Utility: Clean visible text from HTML
def ensure_visible_text(chunk: str) -> str:
    return BeautifulSoup(chunk, "html.parser").get_text(separator=' ', strip=True)

# Utility: Split content into chunks using headers or fallback
def split_html_by_headers(html, header_tags=("h1", "h2", "h3"), fallback_chunk_size=1000, fallback_overlap=200):
    soup = BeautifulSoup(html, "html.parser")
    headers = []
    for tag in header_tags:
        headers += soup.find_all(tag)
    headers = sorted(headers, key=lambda x: x.sourceline if hasattr(x, 'sourceline') and x.sourceline else 0)

    if len(headers) > 1:
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
        return [ensure_visible_text(c) for c in chunks]
    
    # Fallback: use langchain splitter on visible text only
    visible_text = ensure_visible_text(html)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=fallback_chunk_size*8,
        chunk_overlap=fallback_overlap*8
    )
    return splitter.split_text(visible_text)

# Utility: Extract events and relationships as plain text
async def extract_page_metadata_text(page):
    events = getattr(page, "events", None)
    if events is None and hasattr(page, "session"):
        event_result = await page.session.execute(
            select(PageKeyEvent).where(PageKeyEvent.page_id == page.id)
        )
        events = list(event_result.scalars())
    event_lines = [
        f"EVENT [{e.event_type}] on {e.event_date}: {e.summary or ''}" for e in (events or [])
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

# Main function
async def analyze_page(session, agent, page) -> dict:
    # --- 1. Load context and data ---
    valid_embeds = await _get_agent_embeddings(session, agent)
    if not valid_embeds:
        return {"suggestions": [], "error": "Agent world embeddings missing"}

    concepts = await crud_concept.get_concepts(session, gameworld_id=page.gameworld_id, auto_generated=True)
    concept_defs = {c.name: c.description or "" for c in concepts}
    concepts_by_id = {c.id: c for c in concepts}
    concepts_by_name = {normalize_name(c.name): c for c in concepts}
    existing_pages = await crud_page.get_pages(session, gameworld_id=page.gameworld_id)
    existing_titles_norm = {normalize_name(p.name): p for p in existing_pages if p.name}

    # --- 2. Extract metadata and split content into chunks ---
    page_metadata_text = await extract_page_metadata_text(page)
    page_chunks = split_html_by_headers(page.content or "")
    chunk_texts = [ensure_visible_text(chunk) for chunk in page_chunks if chunk and chunk.strip()]

    # --- 3. Prepare LLM chain ---
    prompt = ChatPromptTemplate.from_messages([
        ("system",
            "You are an assistant of my RPG wiki. Your job is to propose new pages that need to be added or pages to be updated on the wiki.\n"
            "You do this, by analyzing a novelized session I am sending you, and suggesting important pages that need to be created or updated\n"
            "I am sending you the story in chunks, so it is easier to process.\n"
            "You are also provided with EXTRA INFORMATION (key events and relationships associated with the full story, not only the chunk). "
            "You may use this extra information to help you decide the importance or meaning of concepts you extract, but focus on the chunk text.\n"
            "Extract important concepts and suggest the canonical page names for each, along with the concept (category/type) each belongs to, using the most complete and unambiguous name possible."
            "Only extract suggested pages that are important for this chunk!\n"
            "\nIf possible, map aliases and short references to their main page name."
            "\nThese is the list of existing Concepts. Suggest pages based only on these concepts:\n"
            + "\n".join(f"- {name}: {desc}" for name, desc in concept_defs.items())
            + "\nReturn a JSON object mapping each page name to its concept (category), e.g.:\n"
            + "{{\n  \"Barão de Karst\": \"NPC\",\n  \"Abelardo\": \"NPC\",\n  \"Yudennach\": \"Reino\",\n  \"Green Dragon\": \"Monstro\"\n}}"
            + "\nDo not include mentions that are not mapped to a concept. Only return the JSON object."
        ),
        ("user", "{chunk}")
    ])
    llm = ChatOpenAI(api_key=settings.openai_api_key, model=settings.open_ai_model)
    chain = prompt | llm

    # --- 4. Process each chunk (prepend metadata) ---
    async def process_chunk(chunk_text):
        chunk_input = (
            f"--- EXTRA INFORMATION ---\n{page_metadata_text}\n\n--- MAIN CHUNK ---\n{chunk_text}"
            if page_metadata_text else chunk_text
        )
        try:
            result = await chain.ainvoke({"chunk": chunk_input})
            parsed = json.loads(result.content.strip())
            if isinstance(parsed, dict):
                return parsed.items()
        except Exception as e:
            print(f"Chunk LLM failed: {e}")
        return []

    all_pairs = []
    if chunk_texts:
        chunk_results = await asyncio.gather(*(process_chunk(chunk) for chunk in chunk_texts))
        for res in chunk_results:
            all_pairs.extend(res)

    # --- 5. Merge and deduplicate results ---
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
            best_name = page_obj.name
            concept_obj = concepts_by_id.get(page_obj.concept_id) if getattr(page_obj, "concept_id", None) else None
            final_concept_id = concept_obj.id if concept_obj else None
            final_concept_name = concept_obj.name if concept_obj else (all_concepts[0] if all_concepts else "Unknown")
            if page_obj.id in already_handled:
                continue
            already_handled.add(page_obj.id)
        else:
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
    # print (f"Suggestions: {suggestions}")
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


def normalize_name(name):
    return name.strip().lower() if name else ""

async def generate_pages(
    session: "AsyncSession", agent: "Agent", page: "Page", page_specs: List[dict]
) -> dict:
    """Thin wrapper that delegates page generation to agentic workers."""
    from app.crud import crud_agent_write

    return await crud_agent_write.generate_pages(session, agent, page, page_specs)




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
    """Thin wrapper that delegates bulk analysis to agentic workers."""
    from app.crud import crud_agent_write

    return await crud_agent_write.analyze_pages(session, agent, pages)
