from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime
from html import escape
from typing import Any
from uuid import uuid4

from app.integrations.llm.model_policy import LLMTask, ModelPolicy
from app.integrations.llm.openai_client import OpenAIClient
from app.jobs.architect.prompts import (
    ARCHITECT_TIMELINE_FROM_NODE_PROMPT,
    ARCHITECT_TIMELINE_SELECTION_PROMPT,
)

logger = logging.getLogger(__name__)

_TIMELINE_STOPWORDS = {
    "the",
    "and",
    "with",
    "from",
    "that",
    "this",
    "were",
    "when",
    "after",
    "before",
    "into",
    "about",
    "their",
    "during",
    "while",
}


def _normalize_alias(alias: str | None) -> str:
    return (alias or "").strip().lower()


def _normalize_alias_key(alias: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _normalize_alias(alias)).strip()


def _strip_markdown_code_blocks(text: str | None) -> str:
    if not text:
        return ""
    pattern = r"```[a-zA-Z]*\\s*\\n?(.*?)\\n?```"
    cleaned = re.sub(pattern, r"\1", text, flags=re.DOTALL)
    return cleaned.strip()


def _chunk_text(text: str, *, chunk_size: int, overlap: int) -> list[str]:
    words = text.split()
    total = len(words)
    if total <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < total:
        end = min(start + chunk_size, total)
        chunks.append(" ".join(words[start:end]))
        if end >= total:
            break
        start = max(0, end - overlap)
    return chunks


def _detect_temporal_hint(text: str) -> float:
    lowered = (text or "").lower()
    if any(k in lowered for k in ("earlier", "initially", "first", "before")):
        return -0.25
    if any(k in lowered for k in ("later", "afterward", "eventually", "then")):
        return 0.25
    return 0.0


def _parse_timeline_response(raw: str, *, chunk_index: int) -> list[dict[str, Any]]:
    try:
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        payload = json.loads(raw[start : end + 1])
        if not isinstance(payload, list):
            return []
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    fallback_order = 0
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        title = _strip_markdown_code_blocks((entry.get("title") or "").strip())
        description = _strip_markdown_code_blocks(
            (entry.get("description") or "").strip()
        )
        if not title or not description:
            continue
        fallback_order += 1
        try:
            order_value = float(entry.get("order")) if entry.get("order") is not None else float(fallback_order)
        except (TypeError, ValueError):
            order_value = float(fallback_order)

        related_aliases = entry.get("related_aliases") or []
        if isinstance(related_aliases, str):
            related_aliases = [related_aliases]
        if not isinstance(related_aliases, list):
            related_aliases = []

        out.append(
            {
                "title": title,
                "description": description,
                "source_alias": (entry.get("source_alias") or "").strip() or None,
                "related_aliases": [a.strip() for a in related_aliases if isinstance(a, str) and a.strip()],
                "order": order_value,
                "chunk_index": chunk_index,
                "chunk_order": order_value,
                "temporal_hint": _detect_temporal_hint(description),
            }
        )
    return out


def _timeline_event_sort_key(event: dict[str, Any]) -> tuple[float, float, float, str]:
    chunk_rank = float(event.get("chunk_index", 0))
    primary = event.get("chunk_order") if isinstance(event.get("chunk_order"), (int, float)) else event.get("order", 0)
    order_value = float(event.get("order", 0))
    adjustment = float(event.get("temporal_hint", 0.0) or 0.0)
    return (chunk_rank, float(primary) + adjustment, order_value, event.get("title", "").lower())


def _event_theme_key(event: dict[str, Any]) -> str:
    basis = f"{event.get('title', '')} {event.get('description', '')}".lower()
    tokens = re.findall(r"[a-z0-9']+", basis)
    keywords = [token for token in tokens if len(token) > 3 and token not in _TIMELINE_STOPWORDS]
    if not keywords:
        return "general"
    return " ".join(keywords[:2])


def _build_cluster_title(theme_key: str) -> str:
    if theme_key == "general":
        return "Key Developments"
    words = [word.capitalize() for word in theme_key.split()]
    return f"{' '.join(words)} Event"


def _combine_event_group(theme_key: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    ordered_events = sorted(events, key=_timeline_event_sort_key)
    if len(ordered_events) == 1:
        return ordered_events[0]
    representative = ordered_events[0]
    merged_aliases: list[str] = []
    seen: set[str] = set()
    for event in ordered_events:
        for alias in event.get("related_aliases") or []:
            if alias not in seen:
                seen.add(alias)
                merged_aliases.append(alias)
    return {
        "title": _build_cluster_title(theme_key),
        "description": " ".join(
            [f"{event['title']}: {event['description']}" for event in ordered_events]
        ),
        "source_alias": representative.get("source_alias"),
        "related_aliases": merged_aliases,
        "order": representative.get("order"),
        "chunk_index": representative.get("chunk_index"),
        "chunk_order": representative.get("chunk_order"),
        "temporal_hint": representative.get("temporal_hint"),
    }


def _cluster_timeline_events(events: list[dict[str, Any]], *, max_events: int) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        buckets[_event_theme_key(event)].append(event)
    ordered_buckets = sorted(
        buckets.items(),
        key=lambda item: _timeline_event_sort_key(sorted(item[1], key=_timeline_event_sort_key)[0]),
    )
    clustered: list[dict[str, Any]] = []
    for key, bucket in ordered_buckets:
        clustered.append(_combine_event_group(key, bucket))
        if len(clustered) >= max_events:
            break
    return clustered


def dedup_timeline_events(events: list[dict[str, Any]], *, max_events: int) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for event in events:
        title = (event.get("title") or "").strip()
        description = (event.get("description") or "").strip()
        if not title or not description:
            continue
        normalized_key = re.sub(r"\s+", " ", title.lower())
        existing = deduped.get(normalized_key)
        if existing and _timeline_event_sort_key(existing) <= _timeline_event_sort_key(event):
            continue
        deduped[normalized_key] = event

    ordered = sorted(deduped.values(), key=_timeline_event_sort_key)
    if len(ordered) <= max_events:
        return ordered
    clustered = _cluster_timeline_events(ordered, max_events=max_events)
    return clustered or ordered[:max_events]


def _event_information_density(event: dict[str, Any]) -> float:
    title = (event.get("title") or "").strip()
    description = (event.get("description") or "").strip()
    title_tokens = len(re.findall(r"[a-zA-Z0-9']+", title))
    description_tokens = len(re.findall(r"[a-zA-Z0-9']+", description))
    related_count = len(event.get("related_aliases") or [])
    # Penalize very generic abstract titles.
    generic_penalty = 1.0 if re.search(r"\b(determination|development|event|change|moment|conflict)\b", title.lower()) else 0.0
    return (0.7 * min(description_tokens, 40)) + (1.3 * title_tokens) + (2.0 * related_count) - generic_penalty


def _build_stratified_candidate_pool(
    events: list[dict[str, Any]],
    *,
    max_events: int,
) -> list[dict[str, Any]]:
    if not events:
        return []
    ordered = sorted(events, key=_timeline_event_sort_key)
    if len(ordered) <= max_events:
        return ordered

    # Keep a larger pool for the second LLM ranking pass.
    pool_size = min(len(ordered), max(max_events * 4, max_events + 4))
    slots = max(1, pool_size)
    used: set[int] = set()
    selected_indices: list[int] = []

    # Evenly distribute picks across the timeline.
    for slot in range(slots):
        pos = round(slot * (len(ordered) - 1) / max(1, slots - 1))
        window = range(max(0, pos - 2), min(len(ordered), pos + 3))
        best_idx = None
        best_score = float("-inf")
        for idx in window:
            if idx in used:
                continue
            score = _event_information_density(ordered[idx])
            if score > best_score:
                best_score = score
                best_idx = idx
        if best_idx is not None:
            used.add(best_idx)
            selected_indices.append(best_idx)

    pool = [ordered[idx] for idx in sorted(selected_indices)]
    return pool or ordered[:pool_size]


def _parse_timeline_selection_response(raw: str) -> list[dict[str, Any]]:
    try:
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        payload = json.loads(raw[start : end + 1])
        if not isinstance(payload, list):
            return []
    except Exception:
        return []

    output: list[dict[str, Any]] = []
    for idx, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            continue
        title = _strip_markdown_code_blocks((item.get("title") or "").strip())
        description = _strip_markdown_code_blocks((item.get("description") or "").strip())
        if not title or not description:
            continue
        related_aliases = item.get("related_aliases") or []
        if isinstance(related_aliases, str):
            related_aliases = [related_aliases]
        if not isinstance(related_aliases, list):
            related_aliases = []
        output.append(
            {
                "title": title,
                "description": description,
                "source_alias": (item.get("source_alias") or "").strip() or None,
                "related_aliases": [
                    a.strip() for a in related_aliases if isinstance(a, str) and a.strip()
                ],
                "order": float(idx),
                "chunk_index": float(item.get("candidate_index", idx)),
                "chunk_order": float(idx),
                "temporal_hint": _detect_temporal_hint(description),
            }
        )
    return output


async def _select_best_timeline_events_with_llm(
    *,
    llm_client: OpenAIClient,
    model: str,
    entity_alias: str,
    entity_instance_id: str,
    available_aliases: list[str],
    candidates: list[dict[str, Any]],
    max_events: int,
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    payload = []
    for idx, event in enumerate(sorted(candidates, key=_timeline_event_sort_key)):
        payload.append(
            {
                "candidate_index": idx,
                "title": event.get("title"),
                "description": event.get("description"),
                "source_alias": event.get("source_alias"),
                "related_aliases": event.get("related_aliases") or [],
                "timeline_position": {
                    "chunk_index": event.get("chunk_index"),
                    "order": event.get("order"),
                },
            }
        )
    prompt = ARCHITECT_TIMELINE_SELECTION_PROMPT.format(
        entity_alias=escape(entity_alias),
        entity_instance_id=entity_instance_id,
        available_aliases=json.dumps(available_aliases, ensure_ascii=False),
        max_events=max_events,
        candidate_events_json=json.dumps(payload, ensure_ascii=False),
    )
    raw = await llm_client.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
    )
    selected = _parse_timeline_selection_response(raw)
    if not selected:
        # Fallback to deterministic broad coverage across the whole timeline.
        ordered = sorted(candidates, key=_timeline_event_sort_key)
        if len(ordered) <= max_events:
            return ordered
        picks = []
        for i in range(max_events):
            pos = round(i * (len(ordered) - 1) / max(1, max_events - 1))
            picks.append(ordered[pos])
        return picks
    return dedup_timeline_events(selected, max_events=max_events)


def _compose_timeline_text(
    title: str,
    description: str,
    *,
    source_label: str | None,
    related_labels: list[str],
    after_title: str | None,
) -> str:
    lines = [f"Timeline Event: {title}", description]
    if source_label:
        lines.append(f"Source: {source_label}")
    if related_labels:
        lines.append(f"Related: {', '.join(related_labels)}")
    if after_title:
        lines.append(f"Follows: {after_title}")
    return "\n".join(lines)


def _resolve_alias_to_entity_id(
    alias: str | None,
    alias_to_entity_id: dict[str, str],
) -> str | None:
    normalized = _normalize_alias(alias)
    if not normalized:
        return None
    direct = alias_to_entity_id.get(normalized)
    if direct:
        return direct

    normalized_key = _normalize_alias_key(alias)
    if not normalized_key:
        return None
    alias_tokens = set(normalized_key.split())

    best_id: str | None = None
    best_score = 0.0
    for candidate_alias, entity_id in alias_to_entity_id.items():
        candidate_key = _normalize_alias_key(candidate_alias)
        if not candidate_key:
            continue
        if normalized_key in candidate_key or candidate_key in normalized_key:
            score = 1.0
        else:
            candidate_tokens = set(candidate_key.split())
            if not candidate_tokens:
                continue
            overlap = len(alias_tokens.intersection(candidate_tokens))
            score = overlap / max(1, min(len(alias_tokens), len(candidate_tokens)))
        if score > best_score:
            best_score = score
            best_id = entity_id
    return best_id if best_score >= 0.6 else None


def _resolve_aliases_to_ids(
    aliases: list[str],
    alias_to_entity_id: dict[str, str],
) -> list[str]:
    resolved: list[str] = []
    seen: set[str] = set()
    for alias in aliases or []:
        entity_id = _resolve_alias_to_entity_id(alias, alias_to_entity_id)
        if entity_id and entity_id not in seen:
            seen.add(entity_id)
            resolved.append(entity_id)
    return resolved


def _augment_related_aliases_from_text(
    events: list[dict[str, Any]],
    *,
    alias_to_entity_id: dict[str, str],
    source_entity_id: str,
) -> list[dict[str, Any]]:
    if not events:
        return events

    source_aliases = {
        alias
        for alias, mapped_id in alias_to_entity_id.items()
        if mapped_id == source_entity_id
    }
    source_alias_norm = {_normalize_alias(a) for a in source_aliases}
    catalog = [
        alias
        for alias, mapped_id in alias_to_entity_id.items()
        if mapped_id != source_entity_id and alias
    ]

    for event in events:
        existing = event.get("related_aliases") or []
        existing_norm = {_normalize_alias(a) for a in existing if isinstance(a, str)}
        text_blob = f"{event.get('title', '')} {event.get('description', '')}".lower()
        inferred: list[str] = []
        for alias in catalog:
            alias_key = _normalize_alias_key(alias)
            if not alias_key:
                continue
            if alias_key in text_blob and _normalize_alias(alias) not in existing_norm:
                inferred.append(alias)

        combined: list[str] = []
        seen: set[str] = set()
        for alias in [*existing, *inferred]:
            if not isinstance(alias, str):
                continue
            cleaned = alias.strip()
            if not cleaned:
                continue
            norm = _normalize_alias(cleaned)
            if norm in seen or norm in source_alias_norm:
                continue
            seen.add(norm)
            combined.append(cleaned)
        event["related_aliases"] = combined
    return events


async def _attach_timeline_entities(
    graph_session: Any,
    *,
    instance_id: str,
    timeline_event_id: str,
    source_entity_id: str | None,
    related_entity_ids: list[str],
) -> None:
    if source_entity_id:
        await graph_session.run(
            """
            MATCH (event:TimelineEvent {timeline_event_id: $timeline_event_id})
            MATCH (entity:EntityInstance {entity_instance_id: $source_entity_id})
            WHERE event.instance_id = $instance_id
            MERGE (event)-[:SOURCE_ENTITY]->(entity)
            """,
            {
                "timeline_event_id": timeline_event_id,
                "source_entity_id": source_entity_id,
                "instance_id": instance_id,
            },
        )

    valid_related = [rid for rid in related_entity_ids if rid]
    if valid_related:
        await graph_session.run(
            """
            MATCH (event:TimelineEvent {timeline_event_id: $timeline_event_id})
            WHERE event.instance_id = $instance_id
            WITH event
            UNWIND $related_ids AS related_id
            MATCH (entity:EntityInstance {entity_instance_id: related_id})
            MERGE (event)-[:INVOLVES_ENTITY]->(entity)
            """,
            {
                "timeline_event_id": timeline_event_id,
                "instance_id": instance_id,
                "related_ids": valid_related,
            },
        )


async def _link_timeline_order(
    graph_session: Any,
    *,
    instance_id: str,
    timeline_event_id: str,
    before_event_id: str | None,
    after_event_id: str | None,
) -> None:
    if before_event_id:
        await graph_session.run(
            """
            MATCH (current:TimelineEvent {timeline_event_id: $timeline_event_id})
            MATCH (previous:TimelineEvent {timeline_event_id: $before_event_id})
            WHERE current.instance_id = $instance_id AND previous.instance_id = $instance_id
            MERGE (current)-[:FOLLOWS]->(previous)
            """,
            {
                "timeline_event_id": timeline_event_id,
                "before_event_id": before_event_id,
                "instance_id": instance_id,
            },
        )
    if after_event_id:
        await graph_session.run(
            """
            MATCH (current:TimelineEvent {timeline_event_id: $timeline_event_id})
            MATCH (next:TimelineEvent {timeline_event_id: $after_event_id})
            WHERE current.instance_id = $instance_id AND next.instance_id = $instance_id
            MERGE (current)-[:PRECEDES]->(next)
            """,
            {
                "timeline_event_id": timeline_event_id,
                "after_event_id": after_event_id,
                "instance_id": instance_id,
            },
        )


async def _fetch_entity_context(graph_session: Any, entity_instance_id: str) -> dict[str, Any] | None:
    result = await graph_session.run(
        """
        MATCH (inst:OntologyInstance)-[:HAS_ENTITY]->(entity:EntityInstance {entity_instance_id: $entity_instance_id})
        RETURN inst.instance_id AS instance_id,
               inst.ontology_id AS ontology_id,
               entity.entity_instance_id AS entity_instance_id,
               entity.alias AS alias,
               entity.text AS text,
               entity.autogenerated_text AS autogenerated_text
        LIMIT 1
        """,
        {"entity_instance_id": entity_instance_id},
    )
    return await result.single()


async def _fetch_entity_alias_map(graph_session: Any, instance_id: str) -> dict[str, str]:
    result = await graph_session.run(
        """
        MATCH (:OntologyInstance {instance_id: $instance_id})-[:HAS_ENTITY]->(entity:EntityInstance)
        RETURN entity.entity_instance_id AS entity_instance_id,
               entity.alias AS alias
        """,
        {"instance_id": instance_id},
    )
    rows = await result.data()
    alias_to_id: dict[str, str] = {}
    for row in rows:
        alias = _normalize_alias(row.get("alias"))
        entity_id = row.get("entity_instance_id")
        if alias and entity_id:
            alias_to_id[alias] = entity_id
    return alias_to_id


async def _entity_has_timeline_events(graph_session: Any, *, instance_id: str, entity_instance_id: str) -> bool:
    result = await graph_session.run(
        """
        MATCH (:OntologyInstance {instance_id: $instance_id})-[:HAS_TIMELINE_EVENT]->(event:TimelineEvent)
        WHERE event.created_from_entity_id = $entity_instance_id
           OR event.source_entity_id = $entity_instance_id
           OR $entity_instance_id IN coalesce(event.related_entity_ids, [])
           OR EXISTS ((event)-[:SOURCE_ENTITY]->(:EntityInstance {entity_instance_id: $entity_instance_id}))
           OR EXISTS ((event)-[:INVOLVES_ENTITY]->(:EntityInstance {entity_instance_id: $entity_instance_id}))
        RETURN COUNT(event) AS count
        """,
        {"instance_id": instance_id, "entity_instance_id": entity_instance_id},
    )
    record = await result.single()
    return bool((record or {}).get("count", 0) > 0)


async def _fetch_tail_timeline_event(graph_session: Any, instance_id: str) -> dict[str, Any] | None:
    result = await graph_session.run(
        """
        MATCH (:OntologyInstance {instance_id: $instance_id})-[:HAS_TIMELINE_EVENT]->(event:TimelineEvent)
        RETURN event.timeline_event_id AS timeline_event_id,
               event.title AS title,
               event.after_event_id AS after_event_id,
               event.created_at AS created_at
        """,
        {"instance_id": instance_id},
    )
    rows = await result.data()
    if not rows:
        return None
    for row in rows:
        if not row.get("after_event_id"):
            return row
    return rows[-1]


async def generate_timeline_events_for_entity(
    *,
    graph_session: Any,
    llm_client: OpenAIClient,
    model_policy: ModelPolicy,
    entity_instance_id: str,
    max_events: int,
    force: bool,
    chunk_size: int = 1000,
    chunk_overlap: int = 100,
) -> dict[str, Any]:
    entity = await _fetch_entity_context(graph_session, entity_instance_id)
    if not entity:
        raise ValueError("Entity not found")

    instance_id = entity["instance_id"]
    ontology_id = entity["ontology_id"]
    alias = entity.get("alias") or entity_instance_id

    if not force:
        has_events = await _entity_has_timeline_events(
            graph_session, instance_id=instance_id, entity_instance_id=entity_instance_id
        )
        if has_events:
            return {
                "status": "skipped",
                "reason": "existing_events_present",
                "entity_instance_id": entity_instance_id,
                "instance_id": instance_id,
                "created_event_ids": [],
            }

    source_text = (entity.get("text") or "").strip() or (entity.get("autogenerated_text") or "").strip()
    if not source_text:
        raise ValueError("Entity has no usable source text for timeline generation")

    alias_to_entity_id = await _fetch_entity_alias_map(graph_session, instance_id)
    available_aliases = sorted(
        {
            alias_name
            for alias_name, mapped_entity_id in alias_to_entity_id.items()
            if alias_name and mapped_entity_id != entity_instance_id
        }
    )

    chunks = _chunk_text(source_text, chunk_size=chunk_size, overlap=chunk_overlap)
    model = model_policy.get_model(LLMTask.ARCHITECT_EXTRACT)

    extracted_events: list[dict[str, Any]] = []
    for chunk_index, chunk in enumerate(chunks):
        prompt = ARCHITECT_TIMELINE_FROM_NODE_PROMPT.format(
            entity_alias=escape(alias),
            entity_instance_id=entity_instance_id,
            available_aliases=json.dumps(available_aliases, ensure_ascii=False),
            chunk_text=chunk,
            max_events=max_events,
        )
        raw = await llm_client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        extracted_events.extend(_parse_timeline_response(raw, chunk_index=chunk_index))

    deduped_events = dedup_timeline_events(
        extracted_events,
        max_events=max(12, max_events * 4),
    )
    candidate_pool = _build_stratified_candidate_pool(
        deduped_events,
        max_events=max_events,
    )
    timeline_events = await _select_best_timeline_events_with_llm(
        llm_client=llm_client,
        model=model,
        entity_alias=alias,
        entity_instance_id=entity_instance_id,
        available_aliases=available_aliases,
        candidates=candidate_pool,
        max_events=max_events,
    )
    timeline_events = _augment_related_aliases_from_text(
        timeline_events,
        alias_to_entity_id=alias_to_entity_id,
        source_entity_id=entity_instance_id,
    )
    if not timeline_events:
        return {
            "status": "completed",
            "reason": "no_events_extracted",
            "entity_instance_id": entity_instance_id,
            "instance_id": instance_id,
            "created_event_ids": [],
        }

    tail = await _fetch_tail_timeline_event(graph_session, instance_id)
    previous_event_id = tail.get("timeline_event_id") if tail else None
    previous_event_title = tail.get("title") if tail else None

    created_event_ids: list[str] = []
    for event in timeline_events:
        event_id = str(uuid4())
        timestamp = datetime.utcnow().isoformat() + "Z"
        related_ids = _resolve_aliases_to_ids(event.get("related_aliases") or [], alias_to_entity_id)
        related_ids = [rid for rid in related_ids if rid and rid != entity_instance_id]
        text = _compose_timeline_text(
            event["title"],
            event["description"],
            source_label=event.get("source_alias") or alias,
            related_labels=event.get("related_aliases") or [],
            after_title=previous_event_title,
        )

        await graph_session.run(
            """
            MATCH (inst:OntologyInstance {instance_id: $instance_id})
            CREATE (inst)-[:HAS_TIMELINE_EVENT]->(event:TimelineEvent {
                timeline_event_id: $timeline_event_id,
                entity_instance_id: $timeline_event_id,
                instance_id: $instance_id,
                ontology_id: $ontology_id,
                name: $title,
                alias: $title,
                title: $title,
                description: $description,
                created_from_instance_id: $instance_id,
                created_from_entity_id: $created_from_entity_id,
                source_instance_id: $instance_id,
                source_entity_id: $created_from_entity_id,
                related_instance_ids: [],
                related_entity_ids: $related_entity_ids,
                before_event_id: $before_event_id,
                after_event_id: null,
                created_at: $timestamp,
                updated_at: $timestamp,
                last_updated_date: $timestamp,
                text: $text,
                autogenerated_text: $text,
                is_embedded: false,
                last_embedded_date: null
            })
            """,
            {
                "instance_id": instance_id,
                "ontology_id": ontology_id,
                "timeline_event_id": event_id,
                "title": event["title"],
                "description": event["description"],
                "created_from_entity_id": entity_instance_id,
                "related_entity_ids": related_ids,
                "before_event_id": previous_event_id,
                "timestamp": timestamp,
                "text": text,
            },
        )
        await _attach_timeline_entities(
            graph_session,
            instance_id=instance_id,
            timeline_event_id=event_id,
            source_entity_id=entity_instance_id,
            related_entity_ids=related_ids,
        )
        await _link_timeline_order(
            graph_session,
            instance_id=instance_id,
            timeline_event_id=event_id,
            before_event_id=previous_event_id,
            after_event_id=None,
        )
        if previous_event_id:
            await graph_session.run(
                """
                MATCH (event:TimelineEvent {timeline_event_id: $event_id})
                SET event.after_event_id = $after_event_id,
                    event.updated_at = datetime(),
                    event.last_updated_date = datetime(),
                    event.is_embedded = false
                """,
                {"event_id": previous_event_id, "after_event_id": event_id},
            )
            await _link_timeline_order(
                graph_session,
                instance_id=instance_id,
                timeline_event_id=previous_event_id,
                before_event_id=None,
                after_event_id=event_id,
            )

        previous_event_id = event_id
        previous_event_title = event["title"]
        created_event_ids.append(event_id)

    logger.info(
        "architect_timeline_generation: entity=%s instance=%s created_events=%d",
        entity_instance_id,
        instance_id,
        len(created_event_ids),
    )

    return {
        "status": "completed",
        "entity_instance_id": entity_instance_id,
        "instance_id": instance_id,
        "created_event_ids": created_event_ids,
    }
