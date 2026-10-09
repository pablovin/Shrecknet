"""Provider-compatible native structured-output helpers."""

from __future__ import annotations

import re
from typing import Any

from app.integrations.llm.shreckllm_client import ShreckLLMClient


def strict_json_schema(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": schema,
        },
    }


def structured_output_is_unsupported(exc: Exception) -> bool:
    """Fallback only for explicit format incompatibility, never generic failures."""
    status_code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if status_code is not None and status_code not in {400, 422, 501}:
        return False
    if isinstance(exc, (TimeoutError, PermissionError)):
        return False
    message = str(exc).casefold()
    format_name = r"(?:response_format|json_schema|structured outputs?)"
    return bool(re.search(
        rf"(?:does not support|doesn't support|(?:unsupported|unrecognized|unknown)(?: parameter| field| argument| format)?|unexpected keyword argument)"
        rf"\s*:?\s*['\"`]*{format_name}", message,
    ) or re.search(
        rf"{format_name}['\"`]*\s*(?:is\s+)?(?:unsupported|unrecognized)", message,
    ) or re.search(
        rf"{format_name}[^\n.;]{{0,80}}\bnot supported\b", message,
    ))


async def chat_with_structured_output(
    *,
    llm_client: ShreckLLMClient,
    model: Any,
    messages: list[dict[str, str]],
    response_format: dict[str, Any],
    temperature: float,
    usage_tag: str | None = None,
    conversation_id: str | None = None,
    use_conversation_memory: bool = False,
    return_metadata: bool = False,
    max_tokens: int | None = None,
) -> str | dict[str, Any]:
    """Use native structured output, retrying only an explicit unsupported-format failure."""
    common = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "conversation_id": conversation_id,
        "use_conversation_memory": use_conversation_memory,
        "return_metadata": return_metadata,
        "max_tokens": max_tokens,
    }
    try:
        return await llm_client.chat(
            **common,
            usage_tag=usage_tag,
            response_format=response_format,
        )
    except Exception as exc:
        if not structured_output_is_unsupported(exc):
            raise
        fallback_tag = f"{usage_tag}.structured_fallback" if usage_tag else None
        return await llm_client.chat(
            **common,
            usage_tag=fallback_tag,
        )
