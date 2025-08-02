from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from datetime import datetime, timezone
from typing import List, Optional
from pathlib import Path
import json

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from app.config import settings
from app.models.model_agent import Agent

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
