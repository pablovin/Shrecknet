"""Deterministic, dependency-wave retrieval execution for Elder v2."""

from __future__ import annotations

import asyncio
from typing import Any

from app.jobs.elder.schemas import RetrievedChunk
from app.jobs.elder.v2_schemas import RetrievalPlan, RetrievalStep


def labels_for(target: str) -> list[str]:
    return {
        "entity": ["EntityInstance"],
        "scene": ["Scene"],
        "milestone": ["Milestone"],
    }.get(target, ["EntityInstance", "Scene", "Milestone"])


class RetrievalStepError(RuntimeError):
    """Raised when a validated retrieval operation cannot be executed faithfully."""


class ElderRetrievalExecutor:
    def __init__(self, retriever: Any):
        self.retriever = retriever

    async def execute(
        self,
        *,
        plan: RetrievalPlan,
        ontology_ids: list[int],
        instance_id: str | None,
        candidate_limit: int,
        rerank_limit: int,
        entity_bindings: dict[str, list[str]] | None = None,
        definitions: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, list[RetrievedChunk]], list[list[str]], list[dict[str, Any]]]:
        pending = {step.id: step for step in plan.steps}
        results: dict[str, list[RetrievedChunk]] = {}
        waves: list[list[str]] = []
        debug: list[dict[str, Any]] = []
        failed: set[str] = set()
        while pending:
            ready = [step for step in pending.values() if step.dependencies.issubset(results)]
            if not ready:
                raise ValueError("retrieval plan contains a dependency cycle")
            waves.append([step.id for step in ready])
            runnable: list[RetrievalStep] = []
            for step in ready:
                failed_dependencies = sorted(step.dependencies & failed)
                if failed_dependencies:
                    results[step.id] = []
                    failed.add(step.id)
                    debug.append({
                        "step_id": step.id,
                        "operation": step.operation,
                        "status": "skipped_dependency_failed",
                        "failed_dependencies": failed_dependencies,
                    })
                    pending.pop(step.id)
                else:
                    runnable.append(step)
            if not runnable:
                continue
            rows = await asyncio.gather(
                *[
                    self._execute_step(
                        step=step,
                        prior=results,
                        ontology_ids=ontology_ids,
                        instance_id=instance_id,
                        candidate_limit=candidate_limit,
                        rerank_limit=rerank_limit,
                        entity_bindings=entity_bindings or {},
                        definitions=definitions or [],
                    )
                    for step in runnable
                ],
                return_exceptions=True,
            )
            for step, row in zip(runnable, rows):
                if isinstance(row, Exception):
                    results[step.id] = []
                    failed.add(step.id)
                    debug.append({
                        "step_id": step.id, "operation": step.operation,
                        "status": "failed", "error": str(row),
                    })
                else:
                    results[step.id] = row
                    debug.append({
                        "step_id": step.id, "operation": step.operation,
                        "status": "success", "results": len(row),
                    })
                pending.pop(step.id)
        return results, waves, debug

    async def _execute_step(
        self,
        *,
        step: RetrievalStep,
        prior: dict[str, list[RetrievedChunk]],
        ontology_ids: list[int],
        instance_id: str | None,
        candidate_limit: int,
        rerank_limit: int,
        entity_bindings: dict[str, list[str]],
        definitions: list[dict[str, Any]],
    ) -> list[RetrievedChunk]:
        bound_entity_ids: list[str] = []
        for reference in step.entity_refs:
            matches = entity_bindings.get(reference.casefold(), [])
            if len(matches) != 1:
                raise ValueError(f"entity reference {reference!r} is not uniquely resolved")
            bound_entity_ids.extend(matches)
        query = step.query or " ".join(
            chunk.text for dependency in step.dependencies for chunk in prior.get(dependency, [])[:3]
        )
        # Planner steps such as concept selection may intentionally omit a
        # lexical query. Never send an empty string to Lucene/vector retrieval.
        query = query.strip() or step.purpose.strip() or "Retrieve canonical evidence"
        definition_ids = list(step.filters.entity_definition_ids)
        temporal_property_ids: list[int] = []
        wanted_kinds = {value.casefold() for value in step.filters.source_kinds}
        if step.operation == "resolve_concept" and step.query:
            wanted_kinds.add(step.query.casefold())
        for definition in definitions:
            if str(definition.get("name") or "").casefold() not in wanted_kinds:
                continue
            definition_id = definition.get("definition_id", definition.get("id"))
            if definition_id is not None and int(definition_id) not in definition_ids:
                definition_ids.append(int(definition_id))
            for prop in definition.get("properties") or []:
                if str(prop.get("data_type") or "").casefold() == "date":
                    property_id = prop.get("property_id", prop.get("id"))
                    if property_id is not None:
                        temporal_property_ids.append(int(property_id))

        if step.operation == "resolve_concept":
            if not definition_ids:
                raise RetrievalStepError(f"ontology concept {step.query!r} was not resolved")
            # Concepts constrain later structural operations; they are not narrative evidence.
            return []
        if step.operation in {"resolve_entity", "exact_lookup"}:
            chunks = await self.retriever.search_aliases(query, ontology_ids, top_k=step.limit)
        elif step.operation == "select_nodes":
            selector = getattr(self.retriever, "select_nodes", None)
            if not callable(selector):
                raise RetrievalStepError("retriever does not implement select_nodes")
            chunks = await selector(
                ontology_ids=ontology_ids,
                instance_id=instance_id or step.filters.instance_id,
                entity_definition_ids=definition_ids,
                target_data_type=step.target_data_type,
                temporal_mode=step.temporal.mode,
                temporal_ordering=step.temporal.ordering,
                temporal_direction=step.temporal.direction,
                temporal_property_ids=temporal_property_ids,
                limit=step.limit,
            )
        elif step.operation == "expand_temporal_context":
            anchors = {
                chunk.node_id: float(chunk.score)
                for dependency in step.dependencies
                for chunk in prior.get(dependency, [])
                if chunk.node_label == "EntityInstance"
            }
            chunks = await self.retriever.expand_timeline_context(
                query=query,
                ontology_ids=ontology_ids,
                entity_scores=anchors,
                max_scenes=step.limit,
                max_milestones=step.limit,
                max_total=step.limit,
                temporal_mode=step.temporal.mode,
                temporal_ordering=step.temporal.ordering,
                temporal_direction=step.temporal.direction,
            )
        elif step.operation == "traverse_graph":
            anchors = [chunk for dep in step.dependencies for chunk in prior.get(dep, [])]
            if not anchors:
                return []
            traverse = getattr(self.retriever, "traverse_graph", None)
            if not callable(traverse):
                raise RetrievalStepError("retriever does not implement traverse_graph")
            expanded = await traverse(
                anchors=anchors,
                ontology_ids=ontology_ids,
                instance_id=instance_id or step.filters.instance_id,
                relationships=step.traversal.relationships,
                direction=step.traversal.direction,
                depth=step.traversal.depth,
                limit=step.limit,
            )
            chunks = anchors + [
                chunk for chunk in expanded
                if (chunk.node_label, chunk.node_id)
                not in {(anchor.node_label, anchor.node_id) for anchor in anchors}
            ]
        elif step.operation == "hydrate_sources":
            # Hydration is completed centrally after ranking; retain anchors here.
            chunks = [chunk for dep in step.dependencies for chunk in prior.get(dep, [])]
        elif step.operation == "bounded_read_cypher":
            runner = getattr(self.retriever, "run_bounded_read", None)
            if not callable(runner):
                return []
            chunks = await runner(
                step.cypher,
                parameters={
                    **step.parameters,
                    "ontology_ids": ontology_ids,
                    "instance_id": instance_id,
                    "limit": step.limit,
                },
            )
        elif step.operation == "hybrid_search":
            chunks = await self.retriever.search(
                query=query,
                ontology_ids=ontology_ids,
                top_k=min(step.limit, 6) if bound_entity_ids else step.limit,
                node_scope="everything",
                allowed_labels=labels_for(step.target_data_type),
                candidate_limit=candidate_limit,
                rerank_limit=rerank_limit,
            )
            if bound_entity_ids and step.operation == "hybrid_search":
                expand = getattr(self.retriever, "expand_timeline_context", None)
                if callable(expand):
                    graph_context = await expand(
                        query=query,
                        ontology_ids=ontology_ids,
                        entity_scores={entity_id: 1.0 for entity_id in bound_entity_ids},
                        max_scenes=8,
                        max_milestones=8,
                        max_total=8,
                        temporal_mode=step.temporal.mode,
                        temporal_ordering=step.temporal.ordering,
                        temporal_direction=step.temporal.direction,
                    )
                    graph_context = graph_context[:8]
                    seen = {(chunk.node_label, chunk.node_id) for chunk in chunks}
                    chunks.extend(
                        chunk for chunk in graph_context
                        if (chunk.node_label, chunk.node_id) not in seen
                    )
        else:
            raise RetrievalStepError(f"unsupported retrieval operation: {step.operation}")
        if instance_id:
            chunks = [chunk for chunk in chunks if chunk.instance_id == instance_id]
        for chunk in chunks:
            chunk.source = f"elder_v2:{step.operation}"
        return chunks
