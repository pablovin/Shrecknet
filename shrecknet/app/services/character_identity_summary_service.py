"""Generate a missing narrative portrait without rerunning embodiment or changing traits."""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone

from fastapi import HTTPException
from neo4j import AsyncSession, AsyncManagedTransaction
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.core.config_store import get_settings
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.jobs.character_agent.embody_agent import EmbodyAgent
from app.schemas.character_agent import CharacterAgentRead
from app.services.character_agent_service import CharacterAgentService
from app.services.character_embodiment_service import CharacterEmbodimentService


logger = logging.getLogger(__name__)


class CharacterIdentitySummaryService:
    def __init__(self, sql: SqlAsyncSession, graph: AsyncSession) -> None:
        self.characters = CharacterAgentService(sql, graph)
        self.inputs = CharacterEmbodimentService(sql, graph)
        self.graph = graph

    async def generate_missing(self, agent_id: str) -> CharacterAgentRead:
        saved = await self.characters.get_agent(agent_id)
        if saved.identity_description is not None:
            return saved
        inputs = await self.inputs.load_embodiment_input(
            saved.entity_instance_id, saved.ontology_id, include_scenes=False,
        )
        canonical = {**inputs["canonical_identity"], "alias": saved.name}
        # The manual character background is authored input, alongside the entity text.
        canonical["authored_text"] = "\n\n".join(value for value in (
            canonical.get("authored_text"), saved.background_story,
        ) if value)
        settings = get_settings()
        client = ShreckLLMClient(
            base_url=settings.shreckllm_base_url,
            timeout=settings.shreckllm_request_timeout_s,
            max_retries=settings.shreckllm_max_retries,
            chat_job_timeout_s=settings.shreckllm_chat_job_timeout_s,
        )
        try:
            generator = EmbodyAgent(
                llm_client=client,
                character_incorporation_model=settings.model_character_agent_character_incorporation,
                scene_interpretation_model=settings.model_character_agent_scene_interpretation,
                psychological_interpretation_model=settings.model_character_agent_psychological_interpretation,
                validation_retries=settings.character_agent_embodiment_validation_retries,
            )
            identity = await generator.generate_identity_description(
                canonical_identity=canonical, current_profile=inputs["trait_profile"],
                current_aspects=inputs["current_aspects"], current_goals=inputs["current_goals"],
                current_trait_evidence=inputs["trait_evidence"],
                source_entity_id=saved.entity_instance_id, source_entity_alias=saved.name,
            )
        except Exception as exc:
            logger.exception("Character identity summary generation failed for %s", agent_id)
            raise HTTPException(status_code=502, detail="Character summary generation failed; the saved agent is unchanged") from exc
        finally:
            await client.aclose()

        async def persist(tx: AsyncManagedTransaction) -> None:
            # Lock the character before checking for a competing generation or profile edit.
            row = await (await tx.run(
                "MATCH (agent:CharacterAgent {id:$id}) "
                "SET agent.updated_at = agent.updated_at RETURN agent",
                id=agent_id,
            )).single()
            if not row:
                raise HTTPException(status_code=404, detail="CharacterAgent not found")
            current = dict(row["agent"])
            if current.get("identity_description"):
                return
            if datetime.fromisoformat(str(current.get("updated_at")).replace("Z", "+00:00")) != saved.updated_at:
                raise HTTPException(status_code=409, detail="Character changed during summary generation; retry")
            aspects, goals = await self.inputs.load_current_assignments(agent_id, graph=tx)
            def snapshot(items: Iterable[Mapping[str, object]]) -> list[str]:
                return sorted(json.dumps(item, sort_keys=True, default=str) for item in items)
            if snapshot(aspects) != snapshot(inputs["current_aspects"]) or snapshot(goals) != snapshot(inputs["current_goals"]):
                raise HTTPException(status_code=409, detail="Character assignments changed during summary generation; retry")
            await tx.run(
                "MATCH (agent:CharacterAgent {id:$id}) "
                "SET agent.identity_description=$identity, agent.updated_at=$timestamp",
                id=agent_id, identity=json.dumps(identity.model_dump(mode="json"), ensure_ascii=False),
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        await self.graph.execute_write(persist)
        return await self.characters.get_agent(agent_id)
