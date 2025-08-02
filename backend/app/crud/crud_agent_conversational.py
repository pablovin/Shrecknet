from sqlalchemy.ext.asyncio import AsyncSession
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import Graph

from app.config import settings
from app.crud.crud_agent import get_agent, ensure_personality_prompts
from app.models.model_gameworld import GameWorld
from app.agentic_workers import (
    decompose_question,
    query_world_embeddings,
    aggregate_prune_and_dedup,
    validate_response,
)

openai_model = settings.open_ai_model


async def chat_with_agent(
    session: AsyncSession,
    agent_id: int,
    messages: list[dict],
    n_results: int = 5,
    user_nickname: str | None = None,
    max_decomp_questions: int = 8,
) -> dict:
    agent = await get_agent(session, agent_id)
    if not agent or agent.vector_db_update_date is None:
        raise ValueError("Agent unavailable")

    query = messages[-1].get("content", "") if messages else ""
    world = await session.get(GameWorld, agent.world_id)

    # Step 1: decomposition
    sub_questions = await decompose_question(query, max_decomp_questions)

    # Step 2: vector search
    all_results = await query_world_embeddings(
        session,
        agent,
        sub_questions,
        n_results,
        ["narrative", "event", "relationship"],
        12,
    )

    if not all_results:
        return {
            "answer": (
                "The agent found no relevant lore or evidence in the annals of the world. "
                "Try rephrasing your question!"
            ),
            "sources": [],
        }

    # Step 3: aggregate and build context
    context, sources = aggregate_prune_and_dedup(all_results, n_results, max_decomp_questions)

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
        + (
            f"The user you are assisting is named {user_nickname}. Always address them as {user_nickname}.\n"
            if user_nickname
            else ""
        )
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

    # Step 4: validation and retry if needed
    needs_retry = False
    if (
        "i don't know" in answer.lower()
        or "not enough information" in answer.lower()
        or len(answer.strip()) < 30
    ):
        needs_retry = True
    if not await validate_response(query, answer, user_nickname, tone):
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
