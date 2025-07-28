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


async def chat_with_agent(
    session: AsyncSession,
    agent_id: int,
    messages: list[dict],
    n_results: int = 5,
    user_nickname: str | None = None,
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
    collections = [e.collection for e in valid_embeds]

    # Step 1+2: Query understanding (LLM) + load pages
    async def extract_query_structure():
        llm_understand = ChatOpenAI(api_key=settings.openai_api_key, model=openai_model)
        concepts = await crud_concept.get_concepts(session, gameworld_id=agent.world_id)
        concept_names = [c.name for c in concepts if c.name]
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "Analyze the user query and return:\n"
                "- view types (narrative, event, relationship)\n"
                "- semantic rewritten query\n"
                f"- a list of concepts from the following that might help answer the question:\n{', '.join(concept_names)}\n"
                "Respond only with JSON."
            ),
            ("user", "{query}"),
        ])
        chain = prompt | llm_understand
        structured = await chain.ainvoke({"query": query})
        return structured, concepts

    async def load_pages():
        return await crud_page.get_pages(session, gameworld_id=agent.world_id)

    structured_response, all_pages = await asyncio.gather(
        extract_query_structure(), load_pages()
    )
    structured, concepts = structured_response

    try:
        parsed = json.loads(structured.content.strip())
    except Exception:
        parsed = {
            "semantic_query": query,
            "views": ["narrative", "event", "relationship"],
            "concepts": []
        }

    semantic_query = parsed.get("semantic_query", query)
    views = parsed.get("views", ["narrative", "event", "relationship"])
    filters = {}

    # Fuzzy match against page titles (for user queries mentioning specific pages)
    mentioned_pages = []
    query_words = semantic_query.lower().split()
    for p in all_pages:
        name_lower = p.name.lower()
        if any(fuzz.partial_ratio(name_lower, word) > 90 for word in query_words):
            mentioned_pages.append(p.name)
    if mentioned_pages:
        filters["title"] = mentioned_pages

    # Use LLM-selected concepts from the structured response
    if parsed.get("concepts"):
        filters["concept_name"] = parsed["concepts"]

    print(f"FILTERS USED IN QUERY: {filters}")

    # Step 3: Query all embeddings with improved query_world
    all_results = []
    for coll in collections:
        try:
            results = crud_vectordb.query_world(
                agent.world_id,
                semantic_query,
                n_results=n_results * 2,
                views=views,
                filters=filters,
                collection=coll,
                max_chunks_per_page=6  # adjust as needed per context window
            )
            all_results.extend(results)
        except Exception as ex:
            print(f"Query failed for collection {coll}: {ex}")
            continue

    # Step 4: Rank, select, and filter relevant results
    # Sort by first highlight score, then by full document length as fallback
    all_results = sorted(
        all_results,
        key=lambda r: (
            r["highlights"][0]["score"] if r.get("highlights") and r["highlights"][0].get("score") is not None else 0,
            len(r.get("document", ""))),
        reverse=True
    )

    # Take up to n_results most relevant "pages"
    selected_results = all_results[:n_results]

    # For LLM input, you can combine both aggregated doc and top highlights for each page
    context_blocks = []
    for res in selected_results:
        title = res.get("title") or res.get("page_id") or "Untitled"
        # If you want: include highlight chunks for extra relevance (optional, for transparency or debugging)
        # highlights_text = "\n".join([f"{h['content']}" for h in res.get("highlights", [])])
        # For most LLMs, using the aggregate 'document' is best for context:
        context_blocks.append(f"[{title}]: {res['document']}")

    context = "\n\n".join(context_blocks)

    # Build sources with full metadata (page, concept, etc)
    sources = []
    for res in selected_results:
        sources.append({
            "title": res.get("title") or f"Page {res.get('page_id')}",
            "url": f"/worlds/{agent.world_id}/concept/{res.get('concept_id')}/page/{res.get('page_id')}",
            "concept": res.get("concept_name"),
            "concept_id": res.get("concept_id"),
            "page_id": res.get("page_id"),
        })

    print(f"Context: {context}")
    print(f"Sources: {sources}")

    # Step 5: Generate final response
    history_txt = "\n".join(f"{m['role']}: {m['content']}" for m in messages[:-1])
    personalities = [p.strip() for p in (agent.personality or "helpful NPC").split(",") if p.strip()]
    agent_name = agent.name or "Agent"
    prompts = await ensure_personality_prompts(personalities)
    tone = "\n".join(prompts.get(p, "") for p in personalities if prompts.get(p))

    system_prompt = (
        "The agent is a helper to consume data from the world.\n"
        + f"Agent name: {agent_name}\n"
        + f"World system: {world.system}\n"
        + f"World description: {world.description}\n"
        + f"Agent's personality: {tone}\n"
        + (f"The user you are assisting is named {user_nickname}. Always address them as {user_nickname}.\n" if user_nickname else "")
        + "Use the following context and chat history to answer the user's question.\n"
        + "Use the agent's personality to give the tone of your responses. Stick to it, and make it creative!\n"
        + "Do not mention any links in your answer.\n"
        + "If no relevant information is found in the documents, inform the user."
    )

    gen_prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("system", f"Context:\n{context}" if context else "Context: none"),
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
