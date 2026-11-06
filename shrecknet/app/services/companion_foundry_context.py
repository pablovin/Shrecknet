"""Foundry snapshot evidence and explicit table World scope; no live Foundry access.

Snapshots are untrusted client data. This service validates Shrecknet associations,
not the browser's claimed Foundry permissions or revisions.
"""
import json

from app.integrations.companion.retrieval import evidence
from app.jobs.elder.context_budget import estimate_tokens
from app.repositories.character_companion_repository import fail
from app.services.worlds import world_service


def foundry_evidence(snapshot):
    """Stable immutable sources reused by admission, history and synthesis."""
    if not snapshot:
        return []
    table = snapshot.get("table")
    scope = {"foundry_world_id": table["foundry_world_id"] if table else None}
    rows = []
    if table:
        rows.append(evidence("foundry", "table:" + table["foundry_world_id"],
            json.dumps(table, ensure_ascii=False), title="Foundry snapshot · " + table["title"],
            reference={"captured_at": snapshot["captured_at"], **scope}, scope=scope))
    for doc in snapshot["documents"]:
        document_scope = {"foundry_world_id": doc.get("foundry_world_id") or scope["foundry_world_id"]}
        source = evidence("foundry", doc["uuid"], doc["text"],
            title="Foundry snapshot · " + doc["name"],
            reference={key: doc[key] for key in ("uuid", "kind", "captured_at", "source_revision", "parent_uuid")} | document_scope,
            scope=document_scope, structured={"fields": doc["fields"], "links": doc["links"]})
        source["coverage"] = {"status": doc["coverage"], "warnings": ["Client-provided snapshot; not verified live game state."]}
        rows.append(source)
    return rows


async def resolve_foundry_scope(session, snapshot, context):
    """Prevent implicit fallback to another character's World or campaign notes."""
    context = dict(context)
    table = snapshot.get("table")
    identifier = table.get("shrecknet_world_id") if table else None
    if not identifier:
        if context.get("ontology_id") or context.get("chronicle_id"):
            fail(422, "foundry_world_required")
        return {**context, "ontology_id": None, "chronicle_ontology_id": None}
    row = await world_service.get_world_async(session, identifier)
    if row is None:
        fail(422, "foundry_world_unavailable")
    world, ontology_ids = row
    table["shrecknet_world_name"] = world.name
    explicit = context.get("ontology_id")
    if explicit is not None and explicit not in ontology_ids:
        fail(422, "context_scope_mismatch")
    chosen = explicit if explicit is not None else (ontology_ids[0] if len(ontology_ids) == 1 else None)
    return {**context, "ontology_id": chosen, "chronicle_ontology_id": chosen}


def validate_foundry_budget(snapshot, settings):
    rows = foundry_evidence(snapshot)
    if sum(estimate_tokens(json.dumps(row, ensure_ascii=False)) for row in rows) > settings.companion_max_evidence_tokens:
        fail(413, "foundry_context_budget_exceeded")
    return rows
