"""LLM-based worker functions."""

from __future__ import annotations

import asyncio
import json
from typing import Dict, List, Tuple

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from app.config import settings
from app.agentic_ai.agentic_worker_utils import normalize_name, split_html_by_headers
from app.models.model_page import Page

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


async def process_chunks_worker(
    concept_defs: Dict[str, str],
    page_metadata_text: str,
    chunk_texts: List[str],
) -> List[Tuple[str, str]]:
    """Create prompt and process each chunk through the LLM."""
    system_prompt = (
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
        + '{{\n  "Barão de Karst": "NPC",\n  "Abelardo": "NPC",\n  "Yudennach": "Reino",\n  "Green Dragon": "Monstro"\n}}'
        + "\nDo not include mentions that are not mapped to a concept. Only return the JSON object."
    )
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("user", "{chunk}"),
        ]
    )
    llm = ChatOpenAI(api_key=settings.openai_api_key, model=settings.open_ai_model)
    chain = prompt | llm

    async def process_chunk(chunk_text: str) -> List[Tuple[str, str]]:
        chunk_input = (
            f"--- EXTRA INFORMATION ---\n{page_metadata_text}\n\n--- MAIN CHUNK ---\n{chunk_text}"
            if page_metadata_text
            else chunk_text
        )
        try:
            result = await chain.ainvoke({"chunk": chunk_input})
            parsed = json.loads(result.content.strip())
            if isinstance(parsed, dict):
                return list(parsed.items())
        except Exception as e:
            print(f"Chunk LLM failed: {e}")
        return []

    results = await asyncio.gather(*(process_chunk(t) for t in chunk_texts))
    pairs: List[Tuple[str, str]] = []
    for res in results:
        pairs.extend(res)
    return pairs


def merge_and_deduplicate_worker(
    all_pairs: List[Tuple[str, str]],
    page: Page,
    existing_titles_norm: Dict[str, Page],
    concepts_by_id: Dict[int, "Concept"],
    concepts_by_name: Dict[str, "Concept"],
) -> List[dict]:
    from difflib import SequenceMatcher

    def _similar(a: str, b: str) -> bool:
        a, b = a.lower(), b.lower()
        if a in b or b in a:
            return True
        if a in b.split() or b in a.split():
            return True
        return SequenceMatcher(None, a, b).ratio() >= 0.67

    groups: Dict[str, Dict] = {}
    for name, concept_name in all_pairs:
        norm = normalize_name(name)
        groups.setdefault(norm, {"names": [], "concepts": set()})
        groups[norm]["names"].append(name)
        groups[norm]["concepts"].add(concept_name)

    suggestions: List[dict] = []
    already_handled: set[int] = set()
    for norm, data in groups.items():
        all_names = data["names"]
        all_concepts = list(data["concepts"])

        # Step 1: Try exact match
        page_obj = existing_titles_norm.get(norm)
        exists = bool(page_obj)

        # Step 2: If not exact, fuzzy match
        if not exists:
            # Try to find any close match (using concept_id for more safety)
            for exist_norm, exist_page in existing_titles_norm.items():
                if _similar(norm, exist_norm):
                    # If possible, also compare concept/category for more precision
                    sugg_concept_obj = (
                        concepts_by_name.get(normalize_name(all_concepts[0]))
                        if all_concepts
                        else None
                    )
                    if (not exist_page.concept_id) or (
                        sugg_concept_obj
                        and exist_page.concept_id == sugg_concept_obj.id
                    ):
                        page_obj = exist_page
                        exists = True
                        break

        if exists:
            best_name = page_obj.name
            concept_obj = (
                concepts_by_id.get(page_obj.concept_id)
                if getattr(page_obj, "concept_id", None)
                else None
            )
            final_concept_id = concept_obj.id if concept_obj else None
            final_concept_name = (
                concept_obj.name
                if concept_obj
                else (all_concepts[0] if all_concepts else "Unknown")
            )
            if page_obj.id in already_handled:
                continue
            already_handled.add(page_obj.id)
        else:
            best_name = min(all_names, key=len)
            llm_concept = all_concepts[0] if all_concepts else "Unknown"
            concept_obj = concepts_by_name.get(normalize_name(llm_concept))
            final_concept_id = concept_obj.id if concept_obj else None
            final_concept_name = concept_obj.name if concept_obj else llm_concept

        suggestions.append(
            {
                "name": best_name,
                "concept_id": final_concept_id,
                "concept": final_concept_name,
                "mode": "update" if exists else "create",
                "exists": exists,
                "target_page_id": page_obj.id if exists else None,
                "source_pages": [{"id": page.id, "name": page.name}],
                "source_page_ids": [page.id],
                "source_page_updated": (
                    page.updated_at.isoformat() if page.updated_at else ""
                ),
            }
        )

    return suggestions


async def generate_process_chunk_worker(
    llm: ChatOpenAI,
    concept_map: Dict[int, "Concept"],
    sp: Page,
    chunk: str,
    specs: List[dict],
    spec_data: Dict[int, Dict[str, list]],
) -> None:
    pages_instructions = "\n".join(
        (
            f"Page: {s['name']}"
            + (
                f" (also known as: {', '.join(s['aliases'])})"
                if s.get("aliases")
                else ""
            )
            + "\nInstructions: "
            + (
                concept_map[s["concept_id"]].auto_generated_prompt
                or "(no instructions, just summarize relevant content for this page)"
            )
        )
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
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("user", "Text chunk:\n{text}"),
        ]
    )
    chain = prompt | llm
    try:
        resp = await chain.ainvoke({"text": chunk})
        payload = json.loads(resp.content)
    except Exception as e:  # pragma: no cover - network
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


async def generate_process_source_worker(
    sp: Page,
    specs: List[dict],
    llm: ChatOpenAI,
    concept_map: Dict[int, "Concept"],
    spec_data: Dict[int, Dict[str, list]],
) -> None:
    chunks = split_html_by_headers(sp.content or "")
    await asyncio.gather(
        *(
            generate_process_chunk_worker(llm, concept_map, sp, c, specs, spec_data)
            for c in chunks
        )
    )


def make_validator_prompt(
    query: str, answer: str, user_nickname: str | None, tone: str
) -> str:
    checks = [
        "1. Does the answer fully address the user's question?",
        "2. Does the answer address the user directly"
        + (f" as '{user_nickname}'" if user_nickname else "")
        + "?",
        "3. Does the answer maintain the agent's tone/personality? ("
        + (tone or "No special tone")
        + ")",
    ]
    prompt = (
        f"User question: {query}\n"
        f"Proposed answer: {answer}\n"
        "Evaluate the answer based on the following criteria:\n"
        + "\n".join(checks)
        + "\nFor each point, respond with 'yes' or 'no', then summarize briefly in 2-3 sentences."
    )
    return prompt


async def validate_response(
    query: str, answer: str, user_nickname: str | None, tone: str
) -> bool:
    """Return True if the answer passes validation."""
    llm = ChatOpenAI(api_key=settings.openai_api_key, model=openai_model)
    validator_prompt = make_validator_prompt(query, answer, user_nickname, tone)
    resp = await llm.ainvoke(validator_prompt)
    return "no" not in resp.content.lower()
