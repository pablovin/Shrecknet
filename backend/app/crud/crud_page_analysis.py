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
from app.crud import crud_page, crud_concept
from app.crud import crud_characteristic
from app.crud.crud_agent import ensure_personality_prompts
import json


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


async def analyze_page(session: AsyncSession, agent: Agent, page: Page):
    """Analyze page content and return concept-based page suggestions."""
    concepts = await crud_concept.get_concepts(
        session, gameworld_id=page.gameworld_id, auto_generated=True
    )
    concepts_by_id: Dict[int, Concept] = {c.id: c for c in concepts}
    existing_pages = await crud_page.get_pages(session, gameworld_id=page.gameworld_id)
    pages_by_id = {p.id: p for p in existing_pages}
    page_map = {_canonical(p.name): p.id for p in existing_pages}

    content = page.content or ""
    if not content:
        docs = [""]
    else:
        part_len = max(1, len(content) // 4)
        docs = [content[i * part_len:(i + 1) * part_len] for i in range(3)]
        docs.append(content[3 * part_len:])

    llm = ChatOpenAI(api_key=settings.openai_api_key or "sk-test", model=settings.open_ai_model)

    suggestions_by_name: Dict[str, List[dict]] = {}
    for concept in concepts:
        found: set[str] = set()
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                f"Extract the exact names of any {concept.name} explicitly mentioned in the text. {concept.description or ''} Respond with a comma separated list of the names only. If none are present, reply with an empty string.",
            ),
            ("user", "{text}"),
        ])
        chain = prompt | llm
        for chunk in docs:
            resp = await chain.ainvoke({"text": chunk})
            names = {n.strip() for n in resp.content.split(',') if n.strip()}
            found.update(_post_process_names(names))
        for name in sorted(found):
            if not _valid_name(name):
                continue
            key = _select_key(name, suggestions_by_name)
            exists_id = _find_existing_page(key, page_map)
            entry = {
                "name": key,
                "concept_id": concept.id,
                "concept": concept.name,
                "exists": exists_id is not None,
                "mode": "update" if exists_id is not None else "create",
            }
            if exists_id is not None:
                entry["target_page_id"] = exists_id
                target_page = pages_by_id.get(exists_id)
                if target_page:
                    # Ensure the concept info for the target page is loaded
                    if target_page.concept_id not in concepts_by_id:
                        existing_concept = await crud_concept.get_concept(session, target_page.concept_id)
                        if existing_concept:
                            concepts_by_id[target_page.concept_id] = existing_concept
                    entry["concept_id"] = target_page.concept_id
                    entry["concept"] = concepts_by_id[target_page.concept_id].name
            suggestions_by_name.setdefault(key, []).append(entry)

    final_suggestions: List[dict] = []
    for name, entries in suggestions_by_name.items():
        same_concept = len({e["concept_id"] for e in entries}) == 1
        if len(entries) == 1 or same_concept:
            entry = entries[0]
            entry["source_pages"] = [{"id": page.id, "name": page.name}]
            entry["source_page_ids"] = [page.id]
            entry["source_page_updated"] = (
                page.updated_at.isoformat() if page.updated_at else ""
            )
            final_suggestions.append(entry)
            continue

        option_concepts = [concepts_by_id[e["concept_id"]] for e in entries if e["concept_id"] in concepts_by_id]
        if option_concepts:
            best = await _choose_concept(llm, name, page.content or "", option_concepts)
            chosen = next(
                (e for e in entries if e["concept_id"] in concepts_by_id and concepts_by_id[e["concept_id"]].name == best),
                entries[0],
            )
        else:
            chosen = entries[0]
        chosen["source_pages"] = [{"id": page.id, "name": page.name}]
        chosen["source_page_ids"] = [page.id]
        chosen["source_page_updated"] = (
            page.updated_at.isoformat() if page.updated_at else ""
        )
        final_suggestions.append(chosen)

    return {"suggestions": final_suggestions}

async def generate_pages(session: AsyncSession, agent: Agent, page: Page, page_specs: List[dict]):
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
