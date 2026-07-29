"""Unified, lossless Elder v2 evidence assembly."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.jobs.elder.schemas import SourceEvidenceChunk, SourceNode
from app.jobs.elder.context_budget import estimate_tokens, serialize_evidence
from app.jobs.elder.v2_schemas import (
    EVIDENCE_TARGET_TOKENS,
    EvidenceRecord,
    RetrievalPlan,
    SynthesisEvidence,
)
from app.utils.text_sanitization import visible_text


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
    ontology_ids: list[int],
    instance_id: str | None,
) -> tuple[list[EvidenceRecord], list[SourceNode]]:
    by_node: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "chunks": {},
            "methods": set(),
            "score": 0.0,
            "properties": {},
            "temporal_order": None,
        }
    )
    operation_by_step = {step.id: step.operation for step in plan.steps}
    step_index = {step.id: index for index, step in enumerate(plan.steps)}
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
            order_rank = (chunk.properties or {}).get("_elder_order_rank")
            if order_rank is not None:
                candidate_order = (step_index[step_id], int(order_rank))
                if row["temporal_order"] is None or candidate_order < row["temporal_order"]:
                    row["temporal_order"] = candidate_order
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

    ranked = sorted(
        by_node.values(),
        key=lambda row: (
            row["temporal_order"] is None,
            row["temporal_order"] or (0, 0),
            -row["score"],
            row["node_id"],
        ),
    )
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
            hydration_mode="complete_source",
        )

    evidence: list[EvidenceRecord] = []
    sources: list[SourceNode] = []
    for index, row in enumerate(ranked):
        canonical = hydrated.get(row["node_id"], {})
        chunks = sorted(
            canonical.get("chunks") or row["chunks"].values(),
            key=lambda chunk: (chunk.get("chunk_index") is None, chunk.get("chunk_index") or 0),
        )
        # Complete-source hydration already composes canonical text and every chunk.
        # Fall back to chunk text only for retrievers that do not return display_text.
        canonical_text = str(canonical.get("display_text") or "")
        chunk_text = "\n\n".join(str(chunk.get("text") or "") for chunk in chunks)
        display_text = canonical_text or chunk_text
        evidence_id = f"evidence-{index + 1}"
        provenance = json_safe(canonical.get("provenance") or {})
        temporal = json_safe(
            canonical.get("temporal_position")
            or row["properties"].get("temporal_position")
            or {}
        )
        property_values = dict(row["properties"])
        property_values.update(canonical.get("properties") or {})
        property_values.pop("_elder_order_rank", None)
        properties = json_safe(property_values)
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
                canonical_text=record.display_text,
                properties=record.properties,
                evidence_chunks=[
                    SourceEvidenceChunk(
                        chunk_id=chunk.get("chunk_id"),
                        chunk_type=chunk.get("chunk_type"),
                        score=float(chunk.get("score") or row["score"]),
                        text=str(chunk.get("text") or ""),
                    )
                    for chunk in chunks
                ],
            )
        )
    return evidence, sources


def _synthesis_properties(value: Any) -> Any:
    """Keep domain properties while removing execution identifiers and private links."""
    if isinstance(value, dict):
        return {
            str(key): _synthesis_properties(item)
            for key, item in value.items()
            if key not in {"id", "node_id", "instance_id", "ontology_id"}
            and not str(key).endswith("_id")
            and not str(key).endswith("_ids")
            and not str(key).endswith("_url")
            and not str(key).startswith("_")
        }
    if isinstance(value, (list, tuple, set)):
        return [_synthesis_properties(item) for item in value]
    return json_safe(value)


def _deduplicate_text(value: str) -> str:
    """Remove exact/contained hydrated blocks without fuzzy loss of evidence."""
    blocks = [visible_text(block) for block in value.split("\n\n")]
    blocks = [block for block in blocks if block]
    kept: list[str] = []
    normalized: list[str] = []
    for block in sorted(blocks, key=len, reverse=True):
        candidate = " ".join(block.casefold().split())
        if any(candidate == prior or candidate in prior for prior in normalized):
            continue
        kept.append(block)
        normalized.append(candidate)
    original_order = {block: index for index, block in enumerate(blocks)}
    return "\n\n".join(sorted(kept, key=lambda block: original_order[block]))


def project_synthesis_evidence(record: EvidenceRecord) -> SynthesisEvidence:
    provenance = record.provenance or {}
    related_names = [
        visible_text(item.get("entity_name"))
        for item in provenance.get("links") or []
        if isinstance(item, dict) and item.get("entity_name")
    ]
    related_names.extend(
        visible_text(value) for value in provenance.get("associated_entity_names") or []
    )
    related_names.extend(
        visible_text(
            value.get("entity_name") or value.get("name")
            if isinstance(value, dict)
            else value
        )
        for value in record.associated_entities
    )
    return SynthesisEvidence(
        evidence_id=record.evidence_id.split(":", 1)[0],
        source_kind=visible_text(record.source_kind),
        source_name=visible_text(record.display_name),
        text=_deduplicate_text(visible_text(record.display_text)),
        related_entities=list(dict.fromkeys(value for value in related_names if value)),
        canonical_facts=_synthesis_properties(record.properties or {}),
        temporal_position=json_safe(record.temporal_position) or None,
    )


def select_synthesis_evidence(
    records: list[EvidenceRecord], *, evidence_budget_tokens: int
) -> tuple[list[SynthesisEvidence], dict[str, Any]]:
    """Select full, atomic sources through the first source crossing the soft budget."""
    compact: list[SynthesisEvidence] = []
    used_tokens = 0
    crossing_source: str | None = None
    for record in records:
        item = project_synthesis_evidence(record)
        item_tokens = estimate_tokens(serialize_evidence(item))
        compact.append(item)
        used_tokens += item_tokens
        if used_tokens > evidence_budget_tokens:
            crossing_source = record.evidence_id
            break
    stats = {
        "retrieved_sources": len(records),
        "included_sources": len(compact),
        "omitted_sources": max(0, len(records) - len(compact)),
        "evidence_tokens": used_tokens,
        "budget_tokens": evidence_budget_tokens,
        "crossing_source_included": crossing_source is not None,
        "crossing_source": crossing_source,
    }
    return compact, stats


async def assemble_budgeted_evidence(
    *,
    retriever: Any,
    plan: RetrievalPlan,
    step_results: dict[str, list[Any]],
    ontology_ids: list[int],
    instance_id: str | None,
) -> tuple[list[EvidenceRecord], list[SourceNode], list[SynthesisEvidence], dict[str, Any]]:
    """Budget terminal step evidence independently, then merge canonical sources."""
    selected_by_node: dict[str, tuple[EvidenceRecord, SourceNode, SynthesisEvidence]] = {}
    step_stats: list[dict[str, Any]] = []
    steps_by_id = {step.id: step for step in plan.steps}
    for step in plan.terminal_evidence_steps:
        relevant_ids = {step.id}
        pending = list(step.dependencies)
        while pending:
            dependency_id = pending.pop()
            if dependency_id in relevant_ids:
                continue
            relevant_ids.add(dependency_id)
            dependency = steps_by_id.get(dependency_id)
            if dependency is not None:
                pending.extend(dependency.dependencies)
        records, sources = await assemble_evidence(
            retriever=retriever,
            plan=plan,
            step_results={
                step_id: step_results.get(step_id, [])
                for step_id in relevant_ids
            },
            ontology_ids=ontology_ids,
            instance_id=instance_id,
        )
        target = EVIDENCE_TARGET_TOKENS[str(step.evidence_type)]
        compact, stats = select_synthesis_evidence(
            records,
            evidence_budget_tokens=target,
        )
        included = stats["included_sources"]
        for record, source, synthesis in zip(
            records[:included], sources[:included], compact
        ):
            current = selected_by_node.get(record.node_id)
            if current is None or record.score > current[0].score:
                selected_by_node[record.node_id] = (record, source, synthesis)
        step_stats.append(
            {
                "step_id": step.id,
                "evidence_type": step.evidence_type,
                "evidence_target_tokens": target,
                **stats,
            }
        )

    merged = list(selected_by_node.values())
    if not any(record.temporal_position for record, _source, _synthesis in merged):
        merged.sort(key=lambda row: (-row[0].score, row[0].node_id))
    records: list[EvidenceRecord] = []
    sources: list[SourceNode] = []
    synthesis: list[SynthesisEvidence] = []
    for index, (record, source, synthesis_record) in enumerate(merged, start=1):
        evidence_id = f"evidence-{index}"
        records.append(record.model_copy(update={"evidence_id": evidence_id}))
        sources.append(source.model_copy(update={"evidence_id": evidence_id}))
        synthesis.append(
            synthesis_record.model_copy(update={"evidence_id": evidence_id})
        )
    return records, sources, synthesis, {
        "per_step": step_stats,
        "retrieved_sources": sum(row["retrieved_sources"] for row in step_stats),
        "included_sources": len(records),
        "evidence_tokens": sum(estimate_tokens(serialize_evidence(row)) for row in synthesis),
    }
