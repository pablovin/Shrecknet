"""Explicit per-role context preflight; no model-name capability guesses."""
from __future__ import annotations
import json
from typing import Any


def input_cost(prompt: str, schema: dict[str, Any] | None = None) -> int:
    # Byte count deliberately overestimates common byte-based provider tokenizers.
    # Include message/schema framing rather than relying on English chars/4.
    return len(prompt.encode("utf-8")) + len(json.dumps(schema).encode("utf-8")) + 256


def fits_context(prompt: str, schema: dict[str, Any] | None, *, context_tokens: int, output_tokens: int) -> bool:
    return input_cost(prompt, schema) + output_tokens <= int(context_tokens * 0.8)


def require_context(prompt: str, schema: dict[str, Any] | None, *, context_tokens: int, output_tokens: int) -> None:
    if not fits_context(prompt, schema, context_tokens=context_tokens, output_tokens=output_tokens):
        raise ValueError(
            "Novelist input exceeds configured context budget; increase the role's "
            "context setting only if the provider supports it, or supply a shorter source/instructions"
        )
