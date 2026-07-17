"""Single-call retrieval planner and strict plan validation."""

from __future__ import annotations

import json
import re
from typing import Any

from app.jobs.elder.v2_schemas import RetrievalPlan
from app.jobs.elder.prompts import V2_RETRIEVAL_PLANNER_PROMPT
from app.jobs.shrecknet import validate_or_repair_json


_WRITE_OR_UNSAFE = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|ALTER|LOAD\s+CSV|FOREACH|CALL|APOC|DBMS)\b",
    re.IGNORECASE,
)


def validate_bounded_cypher(
    plan: RetrievalPlan, ontology_ids: list[int], instance_id: str | None = None
) -> RetrievalPlan:
    if not ontology_ids:
        raise ValueError("Elder has no assigned ontology scope")
    for step in plan.steps:
        requested_ontologies = set(step.filters.ontology_ids)
        if requested_ontologies and not requested_ontologies.issubset(set(ontology_ids)):
            raise ValueError("retrieval filters exceed the Elder ontology scope")
        if step.filters.instance_id and step.filters.instance_id != instance_id:
            raise ValueError("retrieval filters exceed the active instance scope")
        if not step.filters.ontology_ids:
            step.filters.ontology_ids = list(ontology_ids)
        if instance_id and step.filters.instance_id is None:
            step.filters.instance_id = instance_id
        if step.operation != "bounded_read_cypher":
            continue
        cypher = str(step.cypher or "").strip().rstrip(";")
        if _WRITE_OR_UNSAFE.search(cypher):
            raise ValueError("generated Cypher contains a write or unsafe clause")
        if not re.search(r"\b(MATCH|OPTIONAL\s+MATCH|UNWIND)\b", cypher, re.IGNORECASE):
            raise ValueError("generated Cypher must be a read query")
        if "$ontology_ids" not in cypher:
            raise ValueError("generated Cypher must use the supplied $ontology_ids scope")
        if instance_id and "$instance_id" not in cypher:
            raise ValueError("generated Cypher must use the supplied $instance_id scope")
        if not re.search(r"\bLIMIT\s+(\$limit|\d+)\b", cypher, re.IGNORECASE):
            raise ValueError("generated Cypher must be bounded by LIMIT")
        if not re.search(r"\bRETURN\s+(DISTINCT\s+)?node\b", cypher, re.IGNORECASE):
            raise ValueError("generated Cypher must return the scoped parent as node")
    return plan


def fallback_plan(query: str) -> RetrievalPlan:
    return RetrievalPlan.model_validate(
        {
            "answer_goal": query,
            "steps": [
                {
                    "id": "primary",
                    "operation": "hybrid_search",
                    "query": query,
                    "target_data_type": "mixed",
                    "limit": 20,
                }
            ],
        }
    )


def enforce_complete_source_policy(plan: RetrievalPlan, query: str) -> RetrievalPlan:
    normalized = re.sub(r"\s+", " ", query.casefold()).strip()
    explicitly_complete = bool(
        re.search(r"\b(complete|entire|full|exhaustive|whole)\b", normalized)
        or (
            re.search(r"\bsummari[sz]e\b", normalized)
            and re.search(r"\b(story|chapter|source|document|record)\b", normalized)
        )
    )
    if explicitly_complete and not any(step.operation == "hydrate_sources" for step in plan.steps):
        if len(plan.steps) < 5:
            payload = plan.model_dump()
            payload["steps"].append({
                "id": "hydrate_complete_source",
                "purpose": "Hydrate the explicitly requested complete source",
                "operation": "hydrate_sources",
                "inputs": [step.id for step in plan.steps],
                "hydration_mode": "complete_source",
                "max_tokens_per_source": 100_000,
            })
            plan = RetrievalPlan.model_validate(payload)
    elif not explicitly_complete:
        for step in plan.steps:
            if step.hydration_mode == "complete_source":
                step.hydration_mode = "local_context"
                step.context_chunks_before = 1
                step.context_chunks_after = 1
                step.max_tokens_per_source = min(step.max_tokens_per_source, 1200)
    return plan


def _llm_grounding(grounding: dict[str, Any]) -> dict[str, Any]:
    """Project grounding to readable vocabulary; execution scope never enters the prompt."""
    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: clean(item) for key, item in value.items()
                if key not in {"id", "node_id", "instance_id", "active_instance_id", "ontology_ids"}
                and not key.endswith("_id") and not key.endswith("_ids")
            }
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    return clean({
        "definitions": grounding.get("definitions") or [],
        "resolved_entities": grounding.get("resolved_entities") or [],
    })


def _planner_schema() -> dict[str, Any]:
    """Schema hint without execution-only identifier fields."""
    schema = RetrievalPlan.model_json_schema()

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                value = dict(value)
                value["properties"] = {
                    key: clean(item) for key, item in properties.items()
                    if not key.endswith("_id") and not key.endswith("_ids")
                }
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    return clean(schema)


async def create_retrieval_plan(
    *,
    llm_client: Any,
    model: Any,
    query: str,
    grounding: dict[str, Any],
    repair_model: Any,
    debug: Any = None,
    usage_tag: str = "elder.v2.plan",
) -> RetrievalPlan:
    prompt = V2_RETRIEVAL_PLANNER_PROMPT.format(
        query=query,
        grounding_json=json.dumps(_llm_grounding(grounding), ensure_ascii=False),
    )
    try:
        raw = await llm_client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            usage_tag=usage_tag,
        )
        if debug is not None:
            debug.write("retrieval_planner_llm", input={"prompt": prompt}, output={"raw": raw})
        payload = await validate_or_repair_json(
            llm_client=llm_client,
            model=repair_model,
            raw_text=raw,
            schema_hint=json.dumps(_planner_schema(), ensure_ascii=False),
            usage_tag=f"{usage_tag}.json_repair",
        )
        if debug is not None:
            debug.write(
                "retrieval_planner_json",
                input={"raw": raw, "schema": _planner_schema()},
                output={"parsed_or_repaired": payload},
            )
        plan = validate_bounded_cypher(
            RetrievalPlan.model_validate(payload),
            list(grounding.get("ontology_ids") or []),
            grounding.get("active_instance_id"),
        )
        plan = enforce_complete_source_policy(plan, query)
        if debug is not None:
            debug.write("retrieval_plan_validated", input=payload, output=plan)
        return plan
    except Exception as exc:
        plan = fallback_plan(query)
        if debug is not None:
            debug.write("retrieval_plan_fallback", input={"query": query}, output={"error": str(exc), "plan": plan})
        return plan
