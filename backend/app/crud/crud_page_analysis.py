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
from app.crud import crud_characteristic, crud_agent_embedding
from app.crud.crud_agent import ensure_personality_prompts
import json
from bs4 import BeautifulSoup
from rapidfuzz import fuzz, process


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
):
    chunks = []
    embeddings = await _get_agent_embeddings(session, agent)
    for emb in embeddings:
        try:
            parts = crud_vectordb.query_embedding(
                emb.id,
                agent.world_id,
                query,
                n_results=n_results,
                views=views,
                filters=filters,
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

async def analyze_page(session, agent, page) -> dict:
    valid_embeds = await _get_agent_embeddings(session, agent)
    if not valid_embeds:
        return {"suggestions": [], "error": "Agent world embeddings missing"}

    # Load concepts and existing pages
    concepts = await crud_concept.get_concepts(session, gameworld_id=page.gameworld_id, auto_generated=True)
    concept_defs = {c.name: c.description or "" for c in concepts}
    concepts_by_id = {c.id: c for c in concepts}
    concepts_by_name = {normalize_name(c.name): c for c in concepts}
    existing_pages = await crud_page.get_pages(session, gameworld_id=page.gameworld_id)
    existing_titles_norm = {normalize_name(p.name): p for p in existing_pages if p.name}

    # Chunk page content
    content = page.content or ""
    text_chunks = split_html_by_headers(content)

    # Gather additional context from the agent's world embeddings
    embed_chunks = await _query_agent_world(session, agent, page.name, n_results=3)
    text_chunks.extend(c["document"] for c in embed_chunks)
 
    # Prompt LLM for {page_name: concept_name} mappings
    prompt = ChatPromptTemplate.from_messages([
        ("system",
            "You are analyzing a story. Extract important concepts and suggest the canonical page names for each, along with the concept (category/type) each belongs to, using the most complete and unambiguous name possible."
            "\nIf possible, map aliases and short references to their main page name."
            "\nConcept list:\n"
            + "\n".join(f"- {name}: {desc}" for name, desc in concept_defs.items())
            + "\nReturn a JSON object mapping each page name to its concept (category), e.g.:\n"
            + "{{\n  \"Barão de Karst\": \"NPC\",\n  \"Abelardo\": \"NPC\",\n  \"Yudennach\": \"Reino\",\n  \"Green Dragon\": \"Monstro\"\n}}"
            + "\nDo not include mentions that are not mapped to a concept. Only return the JSON object."
        ),
        ("user", "{chunk}")
    ])
    llm = ChatOpenAI(api_key=settings.openai_api_key, model=settings.open_ai_model)
    chain = prompt | llm

    # Collect all page_name: concept_name pairs from all chunks
    all_pairs = []
    for chunk in text_chunks:
        result = await chain.ainvoke({"chunk": chunk})
        parsed = json.loads(result.content.strip())
        if isinstance(parsed, dict):
            all_pairs.extend(parsed.items())

    # Group by normalized name (deduplicate aliases & similar names)
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
        # Pick the best display name (prefer the actual page name if it exists, else the longest/most descriptive name)
        page_obj = existing_titles_norm.get(norm)
        all_names = data["names"]
        # If any of the LLM names match an existing page exactly, use that page's name. Else, pick the longest one.
        best_name = page_obj.name if page_obj else max(all_names, key=len)

        # If this normalized name already mapped to an existing page, keep only one suggestion for that page!
        if page_obj:
            if page_obj.id in already_handled:
                continue  # Already suggested via another alias
            already_handled.add(page_obj.id)

        # Choose concept from existing page, or from LLM/LLM guess, or from concept list
        concept_obj = None
        if page_obj and getattr(page_obj, "concept_id", None):
            concept_obj = concepts_by_id.get(page_obj.concept_id)
        if not concept_obj:
            # Try: match LLM concept to your concepts list (fuzzy)
            llm_concept = next(iter(data["concepts"])) if data["concepts"] else None
            if llm_concept:
                # Try exact or fuzzy match to available concept types/names
                match = concepts_by_name.get(normalize_name(llm_concept))
                if match:
                    concept_obj = match
        final_concept_id = concept_obj.id if concept_obj else None
        final_concept_name = concept_obj.name if concept_obj else (llm_concept if llm_concept else "Unknown")

        exists = bool(page_obj)
        print(f"Suggestion: {best_name} - Concept: {final_concept_name}  - Exists: {exists}")
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


async def generate_pages(session: AsyncSession, agent: Agent, page: Page, page_specs: List[dict]) -> List[dict]:
    valid_embeds = await _get_agent_embeddings(session, agent)
    if not valid_embeds:
        return []
    # Batch preload everything needed
    concept_ids = {s["concept_id"] for s in page_specs}
    concepts = await crud_concept.get_concepts(session, auto_generated=True)
    concept_map = {c.id: c for c in concepts if c.id in concept_ids}

    all_pages = await crud_page.get_pages(session)
    source_pages = {p.id: p for p in all_pages if p.id in all_pages}

    results = []

    for spec in page_specs:
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

        extra = await _query_agent_world(session, agent, spec["name"], n_results=2)
        if extra:
            joined_sources += "\n" + "\n".join(c["document"] for c in extra)

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
