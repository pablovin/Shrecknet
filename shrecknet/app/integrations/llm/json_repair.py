from __future__ import annotations

from app.core.config_store import LLMModelTarget
from app.integrations.llm.shreckllm_client import ShreckLLMClient

REPAIR_PROMPT_TEMPLATE = """You are a strict JSON repair assistant.

Task:
Repair the following malformed JSON so it becomes valid strict RFC8259 JSON.
Do not change semantic content unless required for JSON validity.

Rules:
- Return ONLY JSON.
- Use double quotes.
- No trailing commas.
- Do not add markdown or explanations.

{schema_hint_block}
Malformed JSON:
{malformed_json}
"""


async def repair_json_text(
    *,
    llm_client: ShreckLLMClient,
    model: str | LLMModelTarget,
    malformed_text: str,
    schema_hint: str | None = None,
    usage_tag: str = "agents.json_repair",
) -> str:
    schema_hint_block = ""
    if schema_hint and str(schema_hint).strip():
        schema_hint_block = f"Expected schema hint:\n{schema_hint.strip()}\n"
    prompt = REPAIR_PROMPT_TEMPLATE.format(
        schema_hint_block=schema_hint_block,
        malformed_json=str(malformed_text or ""),
    )
    response = await llm_client.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        usage_tag=usage_tag,
    )
    return response if isinstance(response, str) else str(response)
