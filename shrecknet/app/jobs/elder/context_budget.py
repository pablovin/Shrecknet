"""Lossless, record-boundary context budgeting for Elder v2."""

from __future__ import annotations

import json

from app.jobs.elder.v2_schemas import EvidenceCapacityError, EvidenceRecord


DEFAULT_CONTEXT_TOKENS = 256_000
DEFAULT_RESERVED_TOKENS = 16_000


def estimate_tokens(value: str) -> int:
    """Use the installed OpenAI-compatible tokenizer, with a conservative fallback."""
    try:
        import tiktoken

        return max(1, len(tiktoken.get_encoding("cl100k_base").encode(value)))
    except Exception:
        return max(1, (len(value.encode("utf-8")) + 2) // 3)


def truncate_tokens(value: str, max_tokens: int) -> str:
    """Bound text without splitting tokenizer units; fallback remains conservative."""
    if estimate_tokens(value) <= max_tokens:
        return value
    try:
        import tiktoken

        encoding = tiktoken.get_encoding("cl100k_base")
        return encoding.decode(encoding.encode(value)[:max_tokens]).rstrip()
    except Exception:
        return value.encode("utf-8")[: max_tokens * 3].decode("utf-8", errors="ignore").rstrip()


def serialize_evidence(record: EvidenceRecord) -> str:
    # Neo4j temporal/spatial values can survive inside the arbitrary canonical
    # properties map. Preserve their complete textual representation instead of
    # failing evidence budgeting or dropping those properties.
    return json.dumps(
        record.model_dump(),
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def partition_complete_records(
    records: list[EvidenceRecord],
    *,
    fixed_prompt: str,
    context_tokens: int = DEFAULT_CONTEXT_TOKENS,
    reserved_tokens: int = DEFAULT_RESERVED_TOKENS,
) -> list[list[EvidenceRecord]]:
    available = context_tokens - reserved_tokens - estimate_tokens(fixed_prompt)
    if available <= 0:
        raise EvidenceCapacityError(
            evidence_id="prompt", required_tokens=estimate_tokens(fixed_prompt), available_tokens=0
        )
    batches: list[list[EvidenceRecord]] = []
    current: list[EvidenceRecord] = []
    current_tokens = 0
    for record in records:
        record_tokens = estimate_tokens(serialize_evidence(record))
        if record_tokens > available:
            raise EvidenceCapacityError(
                evidence_id=record.evidence_id,
                required_tokens=record_tokens,
                available_tokens=available,
            )
        if current and current_tokens + record_tokens > available:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(record)
        current_tokens += record_tokens
    if current:
        batches.append(current)
    return batches or [[]]
