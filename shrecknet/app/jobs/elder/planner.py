"""Single-call retrieval planner and strict plan validation."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.jobs.elder.v2_schemas import RetrievalPlan
from app.jobs.elder.prompts import V2_RETRIEVAL_PLANNER_PROMPT
from app.jobs.shrecknet import repair_invalid_json, validate_or_repair_json
from app.jobs.character_incorporation import normalize_target_language


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


def fallback_plan(query: str, grounding: dict[str, Any] | None = None) -> RetrievalPlan:
    normalized_query = re.sub(r"\s+", " ", query.casefold())
    exact_entities = [
        entity
        for entity in (grounding or {}).get("resolved_entities") or []
        if float(entity.get("confidence") or 0) >= 0.99
        and (alias := str(entity.get("alias") or "").strip().casefold())
        and alias in normalized_query
    ]
    if len(exact_entities) == 1:
        alias = str(exact_entities[0]["alias"]).strip()
        return RetrievalPlan.model_validate(
            {
                "answer_goal": f"Provide a grounded overview of {alias}",
                "target_language": "und",
                "response_scope": "standard",
                "steps": [
                    {
                        "id": "entity_profile",
                        "purpose": f"Retrieve the canonical profile for {alias}",
                        "operation": "exact_lookup",
                        "query": alias,
                        "entity_refs": [alias],
                        "target_data_type": "entity",
                        "limit": 1,
                        "evidence_type": "brief_fact",
                    },
                    {
                        "id": "entity_context",
                        "purpose": f"Retrieve important narrative context involving {alias}",
                        "operation": "hybrid_search",
                        "query": (
                            f"Important characteristics, actions, relationships, "
                            f"and major developments involving {alias}"
                        ),
                        "entity_refs": [alias],
                        "target_data_type": "mixed",
                        "limit": 14,
                        "evidence_type": "standard_summary",
                    },
                ],
            }
        )
    return RetrievalPlan.model_validate(
        {
            "answer_goal": query,
            "target_language": "und",
            "steps": [
                {
                    "id": "primary",
                    "operation": "hybrid_search",
                    "query": query,
                    "target_data_type": "mixed",
                    "limit": 20,
                    "evidence_type": "standard_summary",
                }
            ],
        }
    )


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
                cleaned_properties = {
                    key: clean(item) for key, item in properties.items()
                    if not key.endswith("_id") and not key.endswith("_ids")
                }
                value["properties"] = cleaned_properties
                value["required"] = list(cleaned_properties)
                value["additionalProperties"] = False
            elif value.get("type") == "object":
                value = dict(value)
                value["additionalProperties"] = False
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
        chat_kwargs = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "usage_tag": usage_tag,
        }
        try:
            raw = await llm_client.chat(
                **chat_kwargs,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "elder_retrieval_plan",
                        "strict": True,
                        "schema": _planner_schema(),
                    },
                },
            )
        except TypeError:
            # Compatibility for in-process/test clients predating response_format.
            raw = await llm_client.chat(**chat_kwargs)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 400:
                raise
            raw = await llm_client.chat(**chat_kwargs)
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
        try:
            parsed_plan = RetrievalPlan.model_validate(payload)
        except Exception:
            repaired_raw = await repair_invalid_json(
                llm_client=llm_client,
                model=repair_model,
                malformed_text=json.dumps(payload, ensure_ascii=False),
                schema_hint=json.dumps(_planner_schema(), ensure_ascii=False),
                usage_tag=f"{usage_tag}.schema_repair",
            )
            parsed_plan = RetrievalPlan.model_validate(json.loads(repaired_raw))
        plan = validate_bounded_cypher(
            parsed_plan,
            list(grounding.get("ontology_ids") or []),
            grounding.get("active_instance_id"),
        )
        plan.target_language = normalize_target_language(plan.target_language)
        if debug is not None:
            debug.write("retrieval_plan_validated", input=payload, output=plan)
        return plan
    except Exception as exc:
        plan = fallback_plan(query, grounding)
        if debug is not None:
            debug.write(
                "retrieval_plan_fallback",
                input={"query": query},
                output={
                    "reason": "planner_generation_or_validation_failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "fallback_operation": "hybrid_search",
                    "plan": plan,
                },
            )
        return plan
