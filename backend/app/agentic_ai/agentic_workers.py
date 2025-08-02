"""Reusable agentic worker functions for conversational and writing agents."""

import asyncio
import json
from typing import Iterable, List, Tuple

from langchain_openai import ChatOpenAI

from app.config import settings
from app.crud import crud_vectordb, crud_agent_embedding, crud_page_analysis

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


async def analyze_pages_worker(session, agent, pages):
    """Analyze a batch of pages for an agent."""
    return await crud_page_analysis.analyze_pages_bulk(session, agent, pages)


async def generate_pages_worker(session, agent, page, page_specs):
    """Generate or update pages based on specifications."""
    return await crud_page_analysis.generate_pages(session, agent, page, page_specs)
