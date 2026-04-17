"""Retrieval service for semantic search over Neo4j."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import date, datetime
from typing import Any

from neo4j import AsyncSession as AsyncNeo4jSession
from neo4j.time import Date, DateTime, Duration, Time

from app.graphrag.embedding_service import EmbeddingService


def _normalize_value(value: Any) -> Any:
    """Convert Neo4j temporal/complex values into JSON-serializable equivalents."""
    if isinstance(value, (DateTime, datetime)):
        return value.isoformat()
    if isinstance(value, (Date, date)):
        return value.isoformat()
    if isinstance(value, (Time, Duration)):
        return str(value)
    if isinstance(value, list):
        return [_normalize_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _normalize_value(v) for k, v in value.items()}
    return value


class RetrievalService:
    """Service for semantic retrieval from Neo4j."""

    def __init__(self, graph_session: AsyncNeo4jSession) -> None:
        self.graph_session = graph_session
        self.embedding_service = EmbeddingService(graph_session)

    async def semantic_search(
        self,
        query: str,
        ontology_id: int | None = None,
        k: int = 10,
        score_threshold: float = 0.0,
        include_neighbors: bool = True,
        neighbor_limit: int = 10,
    ) -> dict[str, Any]:
        # Ensure chunk index exists (best-effort)
        try:
            await self.embedding_service.ensure_chunk_vector_index()
        except Exception:
            pass

        # Embed the query
        t_embed_start = time.monotonic()
        loop = asyncio.get_event_loop()
        query_embedding = await loop.run_in_executor(
            None, self.embedding_service.embed_text, query
        )
        t_embed = time.monotonic() - t_embed_start

        search_query = """
        CALL db.index.vector.queryNodes('entity_chunk_vec_idx', $k, $query_embedding)
        YIELD node, score
        MATCH (node)<-[:HAS_CHUNK]-(parent)
        WHERE any(label IN labels(parent) WHERE label IN ['EntityInstance', 'Event'])
          AND score >= $score_threshold
          AND ($ontology_id IS NULL OR toInteger(node['ontology_id']) = toInteger($ontology_id))
        RETURN node AS chunk, parent AS parent, score
        ORDER BY score DESC
        LIMIT $k
        """

        t_query_start = time.monotonic()
        result = await self.graph_session.run(
            search_query,
            k=k * 2,  # still fetch extra
            query_embedding=query_embedding,
            score_threshold=score_threshold,
            ontology_id=ontology_id,
        )
        records = await result.data()
        t_query = time.monotonic() - t_query_start

        if not records:
            return {
                "query": query,
                "results": [],
                "total": 0,
                "ontology_id": ontology_id,
            }

        nodes_data: list[dict[str, Any]] = []

        # NEW: keep track of which entity/instance we already added
        seen_ids: set[str] = set()

        for record in records:  # iterate over all (already sorted by score desc)
            chunk = record.get("chunk") or record.get("node")
            parent = record.get("parent")
            score = record["score"]

            def _get(n, key, default=None):
                try:
                    return n.get(key, default)
                except AttributeError:
                    try:
                        return getattr(n, key)
                    except Exception:
                        return default

            try:
                labels_list = list(parent.labels)
            except Exception:
                labels_list = _get(parent, "labels", []) or []

            try:
                parent_props = dict(parent)
            except Exception:
                parent_props = _get(parent, "properties", {}) or {}
            parent_props = {
                k: _normalize_value(v) for k, v in (parent_props or {}).items()
            }

            alias = parent_props.get("alias") or _get(parent, "alias")

            raw_properties = parent_props.get("properties")
            if isinstance(raw_properties, str):
                try:
                    parsed_properties = json.loads(raw_properties)
                except json.JSONDecodeError:
                    parsed_properties = {}
            elif isinstance(raw_properties, dict):
                parsed_properties = raw_properties
            else:
                parsed_properties = {}

            try:
                chunk_props = dict(chunk)
            except Exception:
                chunk_props = _get(chunk, "properties", {}) or {}
            chunk_props = {
                k: _normalize_value(v) for k, v in (chunk_props or {}).items()
            }

            chunk_text = (
                chunk_props.get("text_chunk") or _get(chunk, "text_chunk") or ""
            )

            # this is the key we'll dedupe on
            node_id = _get(parent, "entity_instance_id") or _get(parent, "instance_id")

            # if we already added this entity, skip
            if node_id and node_id in seen_ids:
                continue

            node_info = {
                "node_id": node_id,
                "name": _get(parent, "name"),
                "alias": alias,
                "instance_id": _get(parent, "instance_id"),
                "labels": labels_list,
                "score": score,
                "context_text": chunk_text,
                "chunk_id": chunk_props.get("chunk_id"),
                "chunk_type": chunk_props.get("chunk_type"),
                "chunk_index": chunk_props.get("chunk_index"),
                "text": _get(parent, "text"),
                "autogenerated_text": _get(parent, "autogenerated_text"),
                "ontology_id": _get(parent, "ontology_id"),
                "properties": {
                    **{k: v for k, v in parent_props.items() if k != "properties"},
                    "properties": {
                        k: _normalize_value(v) for k, v in parsed_properties.items()
                    },
                },
            }

            if include_neighbors and node_id:
                neighbors = await self._fetch_neighbors(node_id, neighbor_limit)
                node_info["neighbors"] = neighbors

            nodes_data.append(node_info)

            # mark as seen
            if node_id:
                seen_ids.add(node_id)

            # stop when we reached k uniques
            if len(nodes_data) >= k:
                break

        out = {
            "query": query,
            "results": nodes_data,
            "total": len(nodes_data),
            "ontology_id": ontology_id,
        }

        try:
            import logging

            logger = logging.getLogger(__name__)
            logger.info(
                "semantic_search_timing: embed=%.3fs query=%.3fs results=%d ontology=%s",
                t_embed,
                t_query,
                len(nodes_data),
                str(ontology_id),
            )
        except Exception:
            pass

        return out

    async def _fetch_neighbors(
        self, node_id: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """
        Fetch neighboring nodes.

        Args:
            node_id: Node ID
            limit: Max neighbors to return

        Returns:
            List of neighbor info dicts
        """
        query = """
        MATCH (n)
        WHERE n['entity_instance_id'] = $node_id
          AND any(label IN labels(n) WHERE label IN ['EntityInstance', 'Event'])
        MATCH (n)-[r]->(m)
        WHERE any(label IN labels(m) WHERE label IN ['EntityInstance', 'Event'])
        RETURN type(r) AS rel_type, 
               m['entity_instance_id'] AS node_id,
               coalesce(m['name'], m['title'], m['alias'], m['entity_instance_id']) AS name,
               CASE
                   WHEN 'Event' IN labels(m) THEN 'TimelineEvent'
                   ELSE head(labels(m))
               END AS label
        LIMIT $limit
        """

        result = await self.graph_session.run(query, node_id=node_id, limit=limit)
        records = await result.data()

        neighbors = []
        for record in records:
            neighbors.append(
                {
                    "rel_type": record["rel_type"],
                    "node_id": record["node_id"],
                    "name": record["name"],
                    "label": record["label"],
                }
            )

        return neighbors

    async def get_context_for_llm(
        self,
        query: str,
        ontology_id: int | None = None,
        k: int = 5,
        score_threshold: float = 0.5,
    ) -> str:
        """
        Get formatted context text for LLM from semantic search.

        Args:
            query: Search query
            ontology_id: Filter by ontology
            k: Number of results
            score_threshold: Minimum score

        Returns:
            Formatted context string
        """
        results = await self.semantic_search(
            query=query,
            ontology_id=ontology_id,
            k=k,
            score_threshold=score_threshold,
            include_neighbors=True,
        )

        if not results["results"]:
            return "No relevant information found."

        context_parts = [f"Query: {query}\n", "Relevant Information:\n"]

        for i, node in enumerate(results["results"], 1):
            context_parts.append(f"\n{i}. {node['name']} (Score: {node['score']:.2f})")

            if node.get("context_text"):
                context_parts.append(f"\n{node['context_text']}")

            if node.get("neighbors"):
                neighbor_names = [n["name"] for n in node["neighbors"][:5]]
                if neighbor_names:
                    context_parts.append(f"\nRelated: {', '.join(neighbor_names)}")

            context_parts.append("\n" + "-" * 40)

        return "\n".join(context_parts)
