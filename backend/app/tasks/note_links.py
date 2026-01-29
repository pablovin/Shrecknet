from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select

from app.celery_app import celery_app
from app.core.config_store import get_settings
from app.db.session import AsyncSessionMaker
from app.graph.neo4j import get_driver
from app.models.note import Note, Response
from app.utils.async_helpers import run_async
from app.utils.linker import STOPWORDS, build_alias_pattern, link_text


def _add_alias_entry(
    alias_map: dict[str, list[dict[str, str | None]]],
    key: str,
    *,
    entity_id: str,
    instance_id: str | None,
    alias: str,
) -> None:
    alias_map.setdefault(key, []).append(
        {"entity_id": entity_id, "instance_id": instance_id, "alias": alias}
    )


def _build_alias_map(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, str | None]]]:
    alias_map: dict[str, list[dict[str, str | None]]] = {}
    for row in rows:
        alias = (row.get("alias") or "").strip()
        if not alias:
            continue
        entity_id = row.get("entity_id")
        instance_id = row.get("instance_id")
        if not entity_id:
            continue
        normalized = alias.lower().strip()
        _add_alias_entry(
            alias_map,
            normalized,
            entity_id=entity_id,
            instance_id=instance_id,
            alias=alias,
        )
        tokens = [t for t in re.split(r"[^a-z0-9]+", normalized) if t]
        for token in tokens:
            if len(token) >= 3 and token not in STOPWORDS:
                _add_alias_entry(
                    alias_map,
                    token,
                    entity_id=entity_id,
                    instance_id=instance_id,
                    alias=alias,
                )
        filtered = [t for t in tokens if t and t not in STOPWORDS]
        for i in range(len(filtered)):
            for j in range(i + 2, len(filtered) + 1):
                gram = " ".join(filtered[i:j])
                _add_alias_entry(
                    alias_map,
                    gram,
                    entity_id=entity_id,
                    instance_id=instance_id,
                    alias=alias,
                )
    return alias_map


async def _fetch_alias_rows(ontology_id: int) -> list[dict[str, Any]]:
    settings = get_settings()
    driver = get_driver()
    async with driver.session(database=settings.neo4j_database) as session:
        res = await session.run(
            """
            MATCH (inst:OntologyInstance {ontology_id: $ontology_id})-[:HAS_ENTITY]->(e:EntityInstance)
            RETURN e.entity_instance_id AS entity_id, e.alias AS alias, inst.instance_id AS instance_id
            """,
            ontology_id=ontology_id,
        )
        return await res.data()


async def _link_for_ontology(
    text: str,
    *,
    ontology_id: int,
    current_entity_id: str,
    current_instance_id: str,
) -> str:
    rows = await _fetch_alias_rows(ontology_id)
    alias_map = _build_alias_map(rows)
    if not alias_map:
        return text
    pattern = build_alias_pattern(alias_map.keys())
    if pattern is None:
        return text
    return link_text(text, alias_map, current_entity_id, current_instance_id, pattern) or text


async def _link_note_impl(note_id: int) -> dict[str, Any]:
    async with AsyncSessionMaker() as session:
        result = await session.execute(select(Note).where(Note.id == note_id))
        note = result.scalar_one_or_none()
        if not note:
            return {"status": "not_found", "note_id": note_id}
        if not note.ontology_id:
            return {"status": "skipped", "note_id": note_id, "reason": "no_ontology"}
        linked = await _link_for_ontology(
            note.content,
            ontology_id=note.ontology_id,
            current_entity_id=f"note:{note.id}",
            current_instance_id=f"note:{note.id}",
        )
        if linked == note.content:
            return {"status": "no_change", "note_id": note_id}
        note.content = linked
        await session.commit()
        return {"status": "updated", "note_id": note_id}


async def _link_response_impl(response_id: int) -> dict[str, Any]:
    async with AsyncSessionMaker() as session:
        result = await session.execute(
            select(Response, Note.ontology_id)
            .join(Note, Note.id == Response.note_id)
            .where(Response.id == response_id)
        )
        row = result.one_or_none()
        if not row:
            return {"status": "not_found", "response_id": response_id}
        response, ontology_id = row
        if not ontology_id:
            return {
                "status": "skipped",
                "response_id": response_id,
                "reason": "no_ontology",
            }
        linked = await _link_for_ontology(
            response.content,
            ontology_id=ontology_id,
            current_entity_id=f"response:{response.id}",
            current_instance_id=f"note:{response.note_id}",
        )
        if linked == response.content:
            return {"status": "no_change", "response_id": response_id}
        response.content = linked
        await session.commit()
        return {"status": "updated", "response_id": response_id}


@celery_app.task(name="notes.link_note")
def link_note(note_id: int) -> dict[str, Any]:
    return run_async(_link_note_impl(note_id))


@celery_app.task(name="notes.link_response")
def link_response(response_id: int) -> dict[str, Any]:
    return run_async(_link_response_impl(response_id))
