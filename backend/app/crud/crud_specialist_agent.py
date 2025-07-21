from datetime import datetime, timezone
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import Graph

from app.config import settings
from app.models.model_agent import Agent
from app.models.model_library_item import LibraryItem
from .crud_agent_library_item import get_items as get_agent_items, get_item_ids
from .crud_library_vectordb import query_items
from .crud_agent import ensure_personality_prompts

openai_model = settings.open_ai_model


async def chat_with_specialist(
    session: AsyncSession,
    agent_id: int,
    messages: List[dict],
    n_results: int = 5,
    user_nickname: str | None = None,
) -> dict:
    """Generate a chat response using the specialist vector database."""
    agent = await session.get(Agent, agent_id)
    if not agent or agent.specialist_update_date is None:
        raise ValueError("Agent unavailable")

    query = messages[-1].get("content", "") if messages else ""
    items = await get_agent_items(session, agent_id)
    item_ids = [it.id for it in items]
    docs = query_items(item_ids, query, max(n_results, 5))

    name_lookup = {it.id: it.name for it in items}

    sources = []
    context_parts = []
    for d in docs:
        iid = d.get("item_id")
        name = name_lookup.get(iid, f"Item {iid}")
        sources.append({"name": name})
        context_parts.append(f"[{name}]\n{d['document']}")
    context = "\n\n".join(context_parts)

    history_txt = "\n".join(f"{m['role']}: {m['content']}" for m in messages[:-1])
    personalities = [p.strip() for p in (agent.personality or "helpful").split(',') if p.strip()]
    agent_name = agent.name or "Specialist"

    prompts = await ensure_personality_prompts(personalities)
    tone = "\n".join(prompts.get(p, "") for p in personalities if prompts.get(p))
    personality = ", ".join(personalities) if personalities else "helpful"

    system_prompt = (
        "You are an expert assistant that consults a knowledge base.\n"
        +f"Agent name: {agent_name}\n"
        +f"Agent's personality: {personality}\n"
        +f"{tone}\n"
        + (f"The user you are assisting is named {user_nickname}. Always address them as {user_nickname}.\n" if user_nickname else "")
        +"Use the following context and chat history to answer the user's question.\n"
        +"Add HTML formatting like <p> or <strong> to make responses pleasant.\n"
        +"If no relevant information is found in the documents, inform the user."
    )

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("system", f"Context:\n{context}" if context else "Context: none"),
            ("system", f"Chat history:\n{history_txt}" if history_txt else "Chat history: none"),
            ("user", "{input}"),
        ]
    )

    llm = ChatOpenAI(api_key=settings.openai_api_key or "sk-test", model=openai_model)
    chain = prompt | llm

    builder = Graph()
    builder.add_node("chat", chain)
    builder.set_entry_point("chat")
    builder.set_finish_point("chat")
    graph = builder.compile()

    response = await graph.ainvoke({"input": query})
    answer = getattr(response, "content", str(response))

    return {"answer": answer, "sources": sources}
