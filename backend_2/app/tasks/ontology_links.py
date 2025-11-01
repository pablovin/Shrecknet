from __future__ import annotations

import asyncio
from typing import Any
import re
import logging

from neo4j import AsyncSession

from app.celery_app import celery_app
from app.core.config import get_settings
from app.graph.neo4j import get_driver
from app.models.background_job import AuthorType, JobType
from app.utils.async_helpers import run_async
from app.utils.job_tracking import (
    create_background_job,
    mark_job_done,
    mark_job_failed,
    mark_job_running,
    update_job_progress,
)
from app.utils.linker import build_alias_pattern, link_text

settings = get_settings()


async def _link_instance_entities(instance_id: str, job_id: int) -> None:
    driver = get_driver()
    async with driver.session(database=settings.neo4j_database) as session:
        logger = logging.getLogger("ontology_linker")
        # Update progress: fetching entities
        await update_job_progress(job_id, 0.1, {"status": "fetching entities"})

        records = await _fetch_entities(session, instance_id)
        if not records:
            await update_job_progress(job_id, 1.0, {"status": "no entities found"})
            return

        # Update progress: building alias map
        await update_job_progress(
            job_id, 0.3, {"status": "building alias map", "entity_count": len(records)}
        )

        # Build alias map from ALL instances under the same ontology_id
        alias_map: dict[str, list[str]] = {}
        # Resolve all aliases for the ontology shared by this instance
        ont_res = await session.run(
            """
            MATCH (inst:OntologyInstance {instance_id: $instance_id})
            RETURN inst.ontology_id AS ontology_id
            """,
            instance_id=instance_id,
        )
        ont_row = await ont_res.single()
        ontology_id = ont_row["ontology_id"] if ont_row else None
        if ontology_id is not None:
            alias_res = await session.run(
                """
                MATCH (inst:OntologyInstance {ontology_id: $ontology_id})-[:HAS_ENTITY]->(e:EntityInstance)
                RETURN e.entity_instance_id AS entity_id, e.alias AS alias, inst.instance_id AS instance_id
                """,
                ontology_id=ontology_id,
            )
            alias_rows = await alias_res.data()
            for row in alias_rows:
                alias = row.get("alias") or ""
                entity_id = row["entity_id"]
                target_instance_id = row.get("instance_id")
                normalized = alias.lower().strip()
                if not normalized:
                    continue
                def _append(key: str) -> None:
                    alias_map.setdefault(key, []).append(
                        {"entity_id": entity_id, "instance_id": target_instance_id, "alias": alias}
                    )
                _append(normalized)
                # Also map word tokens to this entity id for simple similarity
                tokens = [t for t in re.split(r"[^a-z0-9]+", normalized) if t]
                for token in tokens:
                    if len(token) >= 3 and token not in {
                        "mr","mrs","ms","miss","dr","prof","professor","sir","dame",
                        "the","and","of","in","on","at","for","to","a","an","with","by","from"
                    }:
                        _append(token)
                # Add multi-word n-grams (length>=2) excluding honorifics and common stopwords
                filtered = [
                    t
                    for t in tokens
                    if t
                    not in {
                        "mr",
                        "mrs",
                        "ms",
                        "miss",
                        "dr",
                        "prof",
                        "professor",
                        "sir",
                        "dame",
                        "the",
                        "and",
                        "of",
                        "in",
                        "on",
                        "at",
                        "for",
                        "to",
                        "a",
                        "an",
                        "with",
                        "by",
                        "from",
                    }
                ]
                for i in range(len(filtered)):
                    for j in range(i + 2, len(filtered) + 1):
                        gram = " ".join(filtered[i:j])
                        _append(gram)

        pattern = build_alias_pattern(alias_map.keys())
        alias_keys_sample = list(alias_map.keys())[:10]
        # Map of key -> first 2 entity ids for insight
        alias_map_sample = {k: [{"entity_id": t.get("entity_id"), "instance_id": t.get("instance_id"), "alias": t.get("alias") } for t in v[:2]] for k, v in list(alias_map.items())[:10]}
        logger.info(
            "link: instance=%s entities=%d alias_keys=%d sample_keys=%s sample_map=%s",
            instance_id,
            len(records),
            len(alias_map),
            alias_keys_sample,
            alias_map_sample,
        )

        # Update progress: linking text
        await update_job_progress(job_id, 0.5, {"status": "linking text"})

        payload: list[dict[str, Any]] = []
        entities_linked = 0
        for record in records:
            entity_id = record["entity_id"]
            raw_text = record.get("text")
            raw_auto = record.get("autogenerated_text")
            alias = record.get("alias") or ""
            normalized = alias.lower()
            # Log text lengths to ensure full-text processing
            logger.info(
                "link: instance=%s entity=%s text_len=%s auto_len=%s pattern=%s",
                instance_id,
                entity_id,
                len(raw_text) if isinstance(raw_text, str) else None,
                len(raw_auto) if isinstance(raw_auto, str) else None,
                bool(pattern is not None),
            )

            text_linked = link_text(
                raw_text, alias_map, entity_id, instance_id, pattern
            )
            auto_linked = link_text(
                raw_auto, alias_map, entity_id, instance_id, pattern
            )
            if (text_linked is not None and raw_text is not None and text_linked != raw_text) or (
                auto_linked is not None and raw_auto is not None and auto_linked != raw_auto
            ):
                entities_linked += 1
                logger.info(
                    "link: instance=%s entity=%s alias=%s updated_text=%s updated_auto=%s",
                    instance_id,
                    entity_id,
                    normalized,
                    bool(text_linked is not None and raw_text is not None and text_linked != raw_text),
                    bool(
                        auto_linked is not None
                        and raw_auto is not None
                        and auto_linked != raw_auto
                    ),
                )
            payload.append(
                {
                    "entity_id": entity_id,
                    "text_linked": text_linked if text_linked is not None else raw_text,
                    "autogenerated_text_linked": (
                        auto_linked if auto_linked is not None else raw_auto
                    ),
                }
            )

        # Update progress: updating database
        await update_job_progress(job_id, 0.8, {"status": "updating database"})

        await session.run(
            """
            UNWIND $payload AS item
            MATCH (e:EntityInstance {entity_instance_id: item.entity_id})
            SET e.text = item.text_linked,
                e.text_linked = item.text_linked,
                e.autogenerated_text_linked = item.autogenerated_text_linked
            """,
            payload=payload,
        )

        # Final progress update
        logger.info(
            "link: instance=%s completed entities=%d linked=%d",
            instance_id,
            len(payload),
            entities_linked,
        )
        await update_job_progress(
            job_id,
            0.95,
            {
                "status": "completed",
                "entities_linked": entities_linked,
                "entities_total": len(payload),
            },
        )


async def _fetch_entities(
    session: AsyncSession, instance_id: str
) -> list[dict[str, Any]]:
    result = await session.run(
        """
        MATCH (inst:OntologyInstance {instance_id: $instance_id})-[:HAS_ENTITY]->(e:EntityInstance)
        RETURN inst.instance_id AS instance_id,
               e.entity_instance_id AS entity_id,
               e.alias AS alias,
               e.text AS text,
               e.autogenerated_text AS autogenerated_text
        """,
        instance_id=instance_id,
    )
    return await result.data()


@celery_app.task(name="ontology.link_instance")
def link_instance(
    instance_id: str, author_type: str = "agent", author_id: str = "system"
) -> dict[str, Any]:
    """
    Link entity instances within an ontology instance.

    Args:
        instance_id: The ontology instance ID
        author_type: Type of author triggering the job (user or agent)
        author_id: ID of the author

    Returns:
        Dictionary with job results
    """
    job_id = run_async(
        create_background_job(
            author_type=AuthorType(author_type),
            author_id=author_id,
            job_type=JobType.GRAPH_LINK_UPDATE,
            description=f"Linking entities for ontology instance {instance_id}",
            celery_task_id=link_instance.request.id,
            details={"instance_id": instance_id},
        )
    )

    try:
        run_async(mark_job_running(job_id))
        run_async(_link_instance_entities(instance_id, job_id))
        run_async(
            mark_job_done(job_id, {"instance_id": instance_id, "status": "success"})
        )
        return {"job_id": job_id, "instance_id": instance_id, "status": "success"}
    except Exception as e:
        run_async(mark_job_failed(job_id, str(e)))
        raise
