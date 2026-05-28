from __future__ import annotations

from app.core.config_store import LLMModelTarget
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.jobs.shrecknet.agent import repair_invalid_json


async def repair_json_text(
    *,
    llm_client: ShreckLLMClient,
    model: str | LLMModelTarget,
    malformed_text: str,
    schema_hint: str | None = None,
    usage_tag: str = "agents.json_repair",
) -> str:
    return await repair_invalid_json(
        llm_client=llm_client,
        model=model,
        malformed_text=malformed_text,
        schema_hint=schema_hint,
        usage_tag=usage_tag,
    )
