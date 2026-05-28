from __future__ import annotations

import json
import re
from typing import Any

from app.core.config_store import LLMModelTarget
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.jobs.shrecknet.prompts import REPAIR_INVALID_JSON_PROMPT


async def repair_invalid_json(
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
    prompt = REPAIR_INVALID_JSON_PROMPT.format(
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


async def validate_or_repair_json(
    *,
    llm_client: ShreckLLMClient,
    model: str | LLMModelTarget,
    raw_text: str,
    schema_hint: str | None = None,
    usage_tag: str = "agents.json_repair",
) -> Any:
    text = str(raw_text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(r"\{.*\}|\[.*\]", text, flags=re.DOTALL)
    if match:
        candidate = match.group(0)
        try:
            return json.loads(candidate)
        except Exception:
            text = candidate

    repaired = await repair_invalid_json(
        llm_client=llm_client,
        model=model,
        malformed_text=text,
        schema_hint=schema_hint,
        usage_tag=usage_tag,
    )
    return json.loads(repaired)
