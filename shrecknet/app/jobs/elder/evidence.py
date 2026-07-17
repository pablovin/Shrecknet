"""Unified, lossless Elder v2 evidence assembly."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.jobs.elder.schemas import SourceEvidenceChunk, SourceNode
from app.jobs.elder.context_budget import estimate_tokens, truncate_tokens
from app.jobs.elder.v2_schemas import EvidenceRecord, RetrievalPlan, SynthesisEvidence
from app.utils.text_sanitization import visible_text


_SYNTHESIS_FACT_KEYS = {
    "type", "source_type", "entity_type", "ontology_type", "date", "story_date",
    "created_date", "title", "status", "temporal_type", "boundary_type",
}


def json_safe(value: Any) -> Any:
    """Recursively preserve graph values in a FastAPI/Pydantic-safe representation."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return str(value)


async def assemble_evidence(
    *,
    retriever: Any,
    plan: RetrievalPlan,
    step_results: dict[str, list[Any]],
    limit: int,
    ontology_ids: list[int],
    instance_id: str | None,
) -> tuple[list[EvidenceRecord], list[SourceNode]]:
    by_node: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"chunks": {}, "methods": set(), "score": 0.0, "properties": {}}
    )
    operation_by_step = {step.id: step.operation for step in plan.steps}
    hydration_steps = [step for step in plan.steps if step.operation == "hydrate_sources"]
    hydration = hydration_steps[-1] if hydration_steps else None
    for step_id, chunks in step_results.items():
        for chunk in chunks:
            if not chunk.node_id:
                continue
            row = by_node[chunk.node_id]
            row["node_id"] = chunk.node_id
            row["kind"] = chunk.node_label
            row["name"] = chunk.node_name or chunk.node_alias or chunk.node_id
            row["score"] = max(row["score"], float(chunk.score or 0.0))
            row["properties"].update(chunk.properties or {})
            row["methods"].add(operation_by_step[step_id])
            chunk_key = str(chunk.chunk_id or f"text:{len(row['chunks'])}")
            row["chunks"].setdefault(
                chunk_key,
                {
                    "chunk_id": chunk.chunk_id,
                    "chunk_type": chunk.chunk_type,
                    "chunk_index": chunk.chunk_index,
                    "text": chunk.text or "",
                    "score": float(chunk.score or 0.0),
                },
            )

    ranked = sorted(by_node.values(), key=lambda row: (-row["score"], row["node_id"]))[:limit]
    hydrate = getattr(retriever, "hydrate_evidence_nodes", None)
    hydrated: dict[str, dict[str, Any]] = {}
    if callable(hydrate) and ranked:
        matched_chunks = {
            row["node_id"]: [
                chunk.get("chunk_index") for chunk in row["chunks"].values()
                if chunk.get("chunk_index") is not None
            ]
            for row in ranked
        }
        hydrated = await hydrate(
            [row["node_id"] for row in ranked],
            ontology_ids=ontology_ids,
            instance_id=instance_id,
            matched_chunk_indexes=matched_chunks,
            hydration_mode=hydration.hydration_mode if hydration else "local_context",
            context_chunks_before=hydration.context_chunks_before if hydration else 1,
            context_chunks_after=hydration.context_chunks_after if hydration else 1,
            max_tokens_per_source=hydration.max_tokens_per_source if hydration else 1200,
        )

    evidence: list[EvidenceRecord] = []
    sources: list[SourceNode] = []
    for index, row in enumerate(ranked):
        canonical = hydrated.get(row["node_id"], {})
        chunks = sorted(
            canonical.get("chunks") or row["chunks"].values(),
            key=lambda chunk: (chunk.get("chunk_index") is None, chunk.get("chunk_index") or 0),
        )
        # Every selected chunk is preserved in full. Canonical property text is additive.
        canonical_text = str(canonical.get("display_text") or "")
        chunk_text = "\n\n".join(str(chunk.get("text") or "") for chunk in chunks)
        display_text = "\n\n".join(
            dict.fromkeys(part for part in (canonical_text, chunk_text) if part)
        )
        if not hydration or hydration.hydration_mode != "complete_source":
            display_text = truncate_tokens(
                display_text, hydration.max_tokens_per_source if hydration else 1200
            )
        evidence_id = f"evidence-{index + 1}"
        provenance = json_safe(canonical.get("provenance") or {})
        temporal = json_safe(canonical.get("temporal_position") or {})
        properties = json_safe(canonical.get("properties") or row["properties"])
        methods = sorted(row["methods"])
        record = EvidenceRecord(
            evidence_id=evidence_id,
            node_id=row["node_id"],
            source_kind=canonical.get("source_kind") or row.get("kind"),
            display_name=canonical.get("display_name") or row.get("name"),
            display_text=display_text,
            properties=properties,
            associated_entities=json_safe(list(canonical.get("associated_entities") or [])),
            provenance=provenance,
            temporal_position=temporal,
            score=row["score"],
            retrieval_methods=methods,
            complete=True,
        )
        evidence.append(record)
        sources.append(
            SourceNode(
                node_id=record.node_id,
                node_label=record.source_kind,
                node_type=(record.source_kind or "").lower() or None,
                node_name=record.display_name,
                score=record.score,
                evidence_id=evidence_id,
                provenance=provenance,
                temporal_position=temporal,
                retrieval_methods=methods,
                complete=True,
                canonical_text=record.display_text,
                properties=record.properties,
                evidence_chunks=[
                    SourceEvidenceChunk(
                        chunk_id=chunk.get("chunk_id"),
                        chunk_type=chunk.get("chunk_type"),
                        score=float(chunk.get("score") or row["score"]),
                        text=str(chunk.get("text") or ""),
                        complete=True,
                    )
                    for chunk in chunks
                ],
            )
        )
    return evidence, sources


def compact_synthesis_evidence(
    records: list[EvidenceRecord], *, evidence_budget_tokens: int
) -> list[SynthesisEvidence]:
    """Create an identifier-free, deduplicated synthesis projection within a soft budget."""
    compact: list[SynthesisEvidence] = []
    used_tokens = 0
    for record in records:
        properties = record.properties or {}
        facts = {
            str(key): json_safe(value)
            for key, value in properties.items()
            if str(key).lower() in _SYNTHESIS_FACT_KEYS and value not in (None, "", [], {})
        }
        provenance = record.provenance or {}
        related_names = [
            visible_text(item.get("entity_name"))
            for item in provenance.get("links") or []
            if isinstance(item, dict) and item.get("entity_name")
        ]
        related_names.extend(
            visible_text(value) for value in provenance.get("associated_entity_names") or []
        )
        text = visible_text(record.display_text)
        item = SynthesisEvidence(
            evidence_id=record.evidence_id.split(":", 1)[0],
            source_kind=visible_text(record.source_kind),
            source_name=visible_text(record.display_name),
            text=text,
            related_entities=list(dict.fromkeys(value for value in related_names if value)),
            canonical_facts=facts,
            temporal_position=json_safe(record.temporal_position) or None,
        )
        item_tokens = estimate_tokens(item.model_dump_json(exclude_none=True))
        # The budget is deliberately soft: always retain the first useful record and
        # never split a compact record, but stop adding lower-ranked evidence.
        if compact and used_tokens + item_tokens > evidence_budget_tokens:
            break
        compact.append(item)
        used_tokens += item_tokens
    return compact
