from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from datetime import datetime, timezone
from typing import List, Optional

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import Graph

from pathlib import Path
import json

from app.config import settings
from app.crud import crud_vectordb, crud_concept, crud_page, crud_agent_embedding
from app.models.model_agent import Agent
from app.models.model_gameworld import GameWorld
from rapidfuzz import fuzz
import asyncio

from langchain_text_splitters import RecursiveCharacterTextSplitter

openai_model = settings.open_ai_model
PERSONALITY_FILE = Path("./data/personalities_parsing.json")


async def ensure_personality_prompts(personalities: list[str]) -> dict:
    PERSONALITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if PERSONALITY_FILE.is_file():
        with open(PERSONALITY_FILE) as f:
            data = json.load(f)
    else:
        data = {}

    llm = ChatOpenAI(api_key=settings.openai_api_key or "sk-test", model=openai_model)
    prompt = ChatPromptTemplate.from_messages([
        ("system", "Write one short sentence that describes how text should sound when using this personality."),
        ("user", "{personality}"),
    ])
    chain = prompt | llm

    updated = False
    for p in personalities:
        key = p.strip()
        if not key or key in data:
            continue
        try:
            resp = await chain.ainvoke({"personality": key})
            text = resp.content.strip()
        except Exception:
            text = f"Write with a {key} tone."
        data[key] = f"{key} = {text}"
        updated = True

    if updated:
        with open(PERSONALITY_FILE, "w") as f:
            json.dump(data, f, indent=2)

    return data


async def async_query_all_embeddings(
    session, agent, query, n_results, views, max_chunks_per_page
):
    """
    For a given agent, run the vector search for a query on all valid embeddings (collections).
    """
    embeddings = await crud_agent_embedding.get_embeddings(session, agent.id)
    valid_embeds = [e for e in embeddings if e.last_index_time]
    tasks = [
        asyncio.to_thread(
            crud_vectordb.query_world,
            emb.id,
            query,
            n_results,
            views,
            None,  # No filters
            max_chunks_per_page,
        )
        for emb in valid_embeds
    ]
    all_results = await asyncio.gather(*tasks)
    print (f"RESULTS: {all_results}")
    # Flatten and annotate
    results = []
    for res_list in all_results:
        for res in res_list:
            res["_from_collection"] = True
            results.append(res)
    return results

def extract_keywords(subq):
    # Naive: just take all capitalized words and numbers, but you can make it smarter
    return " ".join([w for w in subq.split() if w.istitle() or w.isdigit()])

def make_validator_prompt(query, answer, user_nickname, tone):
    checks = [
        "1. Does the answer fully address the user's question?",
        "2. Does the answer address the user directly" + (f" as '{user_nickname}'" if user_nickname else "") + "?",
        "3. Does the answer maintain the agent's tone/personality? (" + (tone or "No special tone") + ")"
    ]
    prompt = (
        f"User question: {query}\n"
        f"Proposed answer: {answer}\n"
        "Evaluate the answer based on the following criteria:\n"
        + "\n".join(checks) +
        "\nFor each point, respond with 'yes' or 'no', then summarize briefly in 2-3 sentences."
    )
    return prompt

async def chat_with_agent(
    session: AsyncSession,
    agent_id: int,
    messages: list[dict],
    n_results: int = 5,
    user_nickname: str | None = None,
    max_decomp_questions: int = 8,
) -> dict:
    agent = await session.get(Agent, agent_id)
    if not agent or agent.vector_db_update_date is None:
        raise ValueError("Agent unavailable")

    query = messages[-1].get("content", "") if messages else ""
    world = await session.get(GameWorld, agent.world_id)

    embeddings = await crud_agent_embedding.get_embeddings(session, agent_id)
    valid_embeds = [e for e in embeddings if e.last_index_time]
    if not valid_embeds:
        return {
            "answer": (
                "The agent stares into the abyss, lacking any forged lore. "
                "Craft world embeddings before seeking its counsel!"
            ),
            "sources": [],
        }

    # --- STEP 1: LLM DECOMPOSITION ---
    decomp_llm = ChatOpenAI(api_key=settings.openai_api_key, model=openai_model)
    decomp_prompt = (
        "Given the user's question below, break it down into a list of focused research questions or information needs "
        f"that would help answer it. Limit to at most {max_decomp_questions} entries, prefer fewer if possible. "
        "Respond only with a JSON list of strings.\n\n"
        f"User question: {query}\n"
    )
    try:
        decomp_response = await decomp_llm.ainvoke(decomp_prompt)
        sub_questions = json.loads(decomp_response.content.strip())
        if not isinstance(sub_questions, list):
            sub_questions = [query]
        if not sub_questions:
            sub_questions = [query]
    except Exception as ex:
        print("Decomposition step failed, falling back:", ex)
        sub_questions = [query]

    print("Decomposed sub-questions:", sub_questions)

    # --- STEP 2: HYBRID MULTI-QUERY VECTOR SEARCH ON ALL EMBEDDINGS ---
    tasks = []
    for sq in sub_questions:
        # Long (full) sub-question
        tasks.append(
            async_query_all_embeddings(
                session, agent, sq, n_results * 2, ["narrative", "event", "relationship"], 12
            )
        )
        # # Short (keywordified) version for recall
        # keyword_query = extract_keywords(sq)
        # if keyword_query and keyword_query != sq:
        #     tasks.append(
        #         async_query_all_embeddings(
        #             session, agent, keyword_query, n_results * 2, ["narrative", "event", "relationship"], 12
        #         )
        #     )

    # Run all queries in parallel
    results_lists = await asyncio.gather(*tasks)
    all_results = [item for sublist in results_lists for item in sublist]

    if not all_results:
        return {
            "answer": "The agent found no relevant lore or evidence in the annals of the world. Try rephrasing your question!",
            "sources": []
        }

    # --- STEP 3: AGGREGATION, PRUNING, DEDUP ---
    seen_chunks = set()
    deduped_results = []
    for r in all_results:
        # Deduplicate by (page_id, chunk_index) or content
        if r.get("page_id") is not None and r.get("highlights") and r["highlights"]:
            unique_key = (r.get("page_id"), r["highlights"][0]["chunk_index"])
        else:
            unique_key = r.get("document")
        if unique_key not in seen_chunks:
            deduped_results.append(r)
            seen_chunks.add(unique_key)

    # Sort by highlight score or doc length
    deduped_results = sorted(
        deduped_results,
        key=lambda r: (
            r["highlights"][0]["score"] if r.get("highlights") and r["highlights"][0].get("score") is not None else 0,
            len(r.get("document", ""))),
        reverse=True
    )

    # Limit total context size if needed (here: n_results * max_decomp_questions)
    selected_results = deduped_results[:n_results * max_decomp_questions]

    # Annotate context blocks by sub-question (if present)
    context_blocks = []
    for res in selected_results:
        subq = res.get("_from_subquestion", "")
        title = res.get("title") or res.get("page_id") or "Untitled"
        context_blocks.append(f"[{subq}] [{title}]: {res['document']}")

    context = "\n\n".join(context_blocks)

    # Sources for UI
    sources = []
    for res in selected_results:
        sources.append({
            "title": res.get("title") or f"Page {res.get('page_id')}",
            "url": f"/worlds/{agent.world_id}/concept/{res.get('concept_id')}/page/{res.get('page_id')}",
            "concept": res.get("concept_name"),
            "concept_id": res.get("concept_id"),
            "page_id": res.get("page_id"),
        })

    # print(f"LLM Context: {context[:1500]}...")  # print first part for debugging
    # print(f"Sources: {sources}")

    # --- STEP 4: GENERATE LLM RESPONSE ---
    history_txt = "\n".join(f"{m['role']}: {m['content']}" for m in messages[:-1])
    personalities = [p.strip() for p in (agent.personality or "helpful NPC").split(",") if p.strip()]
    agent_name = agent.name or "Agent"
    prompts = await ensure_personality_prompts(personalities)
    tone = "\n".join(prompts.get(p, "") for p in personalities if prompts.get(p))

    system_prompt = (
        "You are a creative, immersive AI agent who helps users explore a rich fictional world.\n"
        + f"Agent name: {agent_name}\n"
        + f"World system: {world.system}\n"
        + f"World description: {world.description}\n"
        + f"Agent's personality: {tone}\n"
        + (f"The user you are assisting is named {user_nickname}. Always address them as {user_nickname}.\n" if user_nickname else "")
        + "Use the following world context and chat history to answer the user's question as thoroughly as possible.\n"
        + "Use your agent's unique personality and creativity, but only use information provided in the context.\n"
        + "Do NOT mention any links in your answer.\n"
        + "If no relevant information is found in the context, admit it, or speculate gently based on the context."
    )

    gen_prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("system", f"World context:\n{context}" if context else "World context: none"),
        ("system", f"Chat history:\n{history_txt}" if history_txt else "Chat history: none"),
        ("user", "{input}"),
    ])
    final_llm = ChatOpenAI(api_key=settings.openai_api_key, model=openai_model)
    chain = gen_prompt | final_llm
    builder = Graph()
    builder.add_node("chat", chain)
    builder.set_entry_point("chat")
    builder.set_finish_point("chat")
    graph = builder.compile()

    response = await graph.ainvoke({"input": query})
    answer = getattr(response, "content", str(response))

    # --- STEP 5: OPTIONAL ANSWER VALIDATION + RETRY (with tone and name) ---
    needs_retry = False
    if "i don't know" in answer.lower() or "not enough information" in answer.lower() or len(answer.strip()) < 30:
        needs_retry = True

    validator_llm = ChatOpenAI(api_key=settings.openai_api_key, model=openai_model)
    validator_prompt = make_validator_prompt(query, answer, user_nickname, tone)
    validation_response = await validator_llm.ainvoke(validator_prompt)
    if "no" in validation_response.content.lower():
        needs_retry = True

    if needs_retry:
        fallback_prompt = (
            system_prompt
            + "\nThe previous attempt did not fully answer the question or was not user-friendly. "
            + "Try again, making sure to address the user"
            + (f" as '{user_nickname}'" if user_nickname else "")
            + ", and to use the agent's unique tone and personality. "
            + "If you don't know the answer, speculate creatively but make clear when you are guessing."
        )
        gen_prompt = ChatPromptTemplate.from_messages([
            ("system", fallback_prompt),
            ("system", f"World context:\n{context}" if context else "World context: none"),
            ("system", f"Chat history:\n{history_txt}" if history_txt else "Chat history: none"),
            ("user", "{input}"),
        ])
        fallback_chain = gen_prompt | final_llm
        builder = Graph()
        builder.add_node("chat", fallback_chain)
        builder.set_entry_point("chat")
        builder.set_finish_point("chat")
        graph = builder.compile()
        response = await graph.ainvoke({"input": query})
        answer = getattr(response, "content", str(response))

    return {"answer": answer, "sources": sources}




async def create_agent(session: AsyncSession, agent: Agent) -> Agent:
    session.add(agent)
    await session.commit()
    await session.refresh(agent)

    personalities = [p.strip() for p in (agent.personality or "").split(",") if p.strip()]
    if personalities:
        await ensure_personality_prompts(personalities)

    return agent

async def get_agent(session: AsyncSession, agent_id: int) -> Optional[Agent]:
    return await session.get(Agent, agent_id)

async def get_agents(session: AsyncSession, world_id: int | None = None) -> List[Agent]:
    stmt = select(Agent)
    if world_id:
        stmt = stmt.where(Agent.world_id == world_id)
    result = await session.execute(stmt)
    return result.scalars().all()

async def update_agent(session: AsyncSession, agent_id: int, updates: dict) -> Optional[Agent]:
    db_agent = await session.get(Agent, agent_id)
    if not db_agent:
        return None
    for k, v in updates.items():
        setattr(db_agent, k, v)
    db_agent.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(db_agent)

    personalities = [p.strip() for p in (db_agent.personality or "").split(",") if p.strip()]
    if personalities:
        await ensure_personality_prompts(personalities)

    return db_agent

async def delete_agent(session: AsyncSession, agent_id: int) -> bool:
    db_agent = await session.get(Agent, agent_id)
    if not db_agent:
        return False
    await session.delete(db_agent)
    await session.commit()
    return True
