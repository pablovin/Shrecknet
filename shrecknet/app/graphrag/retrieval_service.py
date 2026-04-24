"""Retrieval service for semantic search over Neo4j."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
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
        self.neighbor_parallelism_cap = 8

    @staticmethod
    def _is_temporal_query(query_terms: set[str]) -> bool:
        temporal_tokens = {
            "before",
            "after",
            "during",
            "when",
            "earlier",
            "later",
            "changed",
            "change",
            "previous",
            "next",
        }
        return any(token in temporal_tokens for token in query_terms)

    @staticmethod
    def _tokenize_relation(rel_type: str) -> set[str]:
        if not rel_type:
            return set()
        return {
            token
            for token in re.findall(r"[a-z0-9]+", rel_type.replace("_", " ").lower())
            if token
        }

    def _compute_graph_boosts(
        self,
        *,
        label: str,
        node_id: str | None,
        neighbors: list[dict[str, Any]],
        query_terms: set[str],
        temporal_query: bool,
        scene_occurrence_count: int,
    ) -> dict[str, float]:
        if not neighbors:
            return {
                "scene_alignment_boost": 0.0,
                "entity_recurrence_boost": 0.0,
                "relation_label_boost": 0.0,
                "temporal_neighbor_boost": 0.0,
                "graph_total_boost": 0.0,
            }

        neighbor_names = " ".join(str(n.get("name") or "") for n in neighbors).lower()
        aligned_terms = sum(1 for term in query_terms if term in neighbor_names)
        scene_alignment_boost = 0.0
        if label == "Scene" and query_terms:
            scene_alignment_boost = min(0.06, 0.06 * (aligned_terms / max(1, len(query_terms))))

        entity_recurrence_boost = 0.0
        if label == "EntityInstance" and node_id and scene_occurrence_count > 0:
            entity_recurrence_boost = min(0.05, 0.02 + (scene_occurrence_count * 0.015))

        relation_label_hits = 0
        for neighbor in neighbors:
            relation_tokens = self._tokenize_relation(str(neighbor.get("rel_type") or ""))
            if relation_tokens & query_terms:
                relation_label_hits += 1
        relation_label_boost = min(0.04, relation_label_hits * 0.01)

        temporal_neighbor_boost = 0.0
        if temporal_query:
            temporal_rel_tokens = {"before", "after", "during", "next", "previous"}
            temporal_hits = 0
            for neighbor in neighbors:
                relation_tokens = self._tokenize_relation(str(neighbor.get("rel_type") or ""))
                if relation_tokens & temporal_rel_tokens:
                    temporal_hits += 1
            temporal_neighbor_boost = min(0.04, temporal_hits * 0.01)

        graph_total_boost = min(
            0.15,
            scene_alignment_boost
            + entity_recurrence_boost
            + relation_label_boost
            + temporal_neighbor_boost,
        )

        return {
            "scene_alignment_boost": scene_alignment_boost,
            "entity_recurrence_boost": entity_recurrence_boost,
            "relation_label_boost": relation_label_boost,
            "temporal_neighbor_boost": temporal_neighbor_boost,
            "graph_total_boost": graph_total_boost,
        }

    @staticmethod
    def _build_evidence_bundle(
        node_info: dict[str, Any], neighbors: list[dict[str, Any]]
    ) -> dict[str, Any]:
        primary_label = (
            node_info.get("labels", ["EntityInstance"])[0]
            if node_info.get("labels")
            else "EntityInstance"
        )
        related_entities = [
            n for n in neighbors if str(n.get("label") or "") == "EntityInstance"
        ][:5]
        related_scenes = [n for n in neighbors if str(n.get("label") or "") == "Scene"][:5]
        related_milestones = [
            n for n in neighbors if str(n.get("label") or "") == "Milestone"
        ][:5]

        return {
            "parent_type": primary_label,
            "parent_id": node_info.get("node_id"),
            "parent_name": node_info.get("name") or node_info.get("alias") or node_info.get("node_id"),
            "top_chunk": {
                "chunk_id": node_info.get("chunk_id"),
                "chunk_type": node_info.get("chunk_type"),
                "chunk_index": node_info.get("chunk_index"),
                "text": node_info.get("context_text") or "",
            },
            "related_entities": related_entities,
            "related_scenes": related_scenes,
            "related_milestones": related_milestones,
            "bundle_importance": float(node_info.get("importance_index") or 0.0),
        }

    async def semantic_search(
        self,
        query: str,
        ontology_id: int | None = None,
        k: int = 10,
        score_threshold: float = 0.0,
        include_neighbors: bool = True,
        neighbor_limit: int = 10,
        node_scope: str = "everything",
        allowed_labels: list[str] | None = None,
        candidate_limit: int | None = None,
        rerank_limit: int | None = None,
    ) -> dict[str, Any]:
        t_total_start = time.monotonic()
        print(
            f"[RETRIEVAL] step=start query='{query[:120]}' ontology_id={ontology_id} "
            f"k={k} node_scope={node_scope} candidate_limit={candidate_limit} rerank_limit={rerank_limit}"
        )

        # Ensure chunk index exists (best-effort)
        t_index_start = time.monotonic()
        try:
            await self.embedding_service.ensure_chunk_vector_index()
        except Exception:
            pass
        t_index = time.monotonic() - t_index_start
        print(
            f"[RETRIEVAL] step=ensure_chunk_index duration_s={t_index:.3f}"
        )

        # Embed the query
        t_embed_start = time.monotonic()
        logger = logging.getLogger(__name__)
        logger.info(
            "retrieval_embed_dispatched query='%s' ontology=%s",
            query[:160],
            str(ontology_id),
        )
        loop = asyncio.get_event_loop()
        query_embedding = await loop.run_in_executor(
            None, self.embedding_service.embed_text, query
        )
        t_embed = time.monotonic() - t_embed_start
        print(
            f"[RETRIEVAL] step=embed_query duration_s={t_embed:.3f}"
        )
        logger.info(
            "retrieval_embed_completed query='%s' ontology=%s embed_duration_ms=%.2f",
            query[:160],
            str(ontology_id),
            round(t_embed * 1000, 2),
        )

        node_scope = (node_scope or "everything").strip().lower()
        effective_allowed_labels = allowed_labels or [
            "EntityInstance",
            "Scene",
            "Milestone",
        ]
        if allowed_labels is None:
            if node_scope == "entity":
                effective_allowed_labels = ["EntityInstance"]
            elif node_scope == "scene":
                effective_allowed_labels = ["Scene"]
            elif node_scope == "milestone":
                effective_allowed_labels = ["Milestone"]

        candidate_k = max(k * 4, k)
        if candidate_limit is not None:
            candidate_k = max(k, candidate_limit)

        search_query = """
        CALL db.index.vector.queryNodes('entity_chunk_vec_idx', $k, $query_embedding)
        YIELD node, score
        MATCH (node)<-[:HAS_CHUNK]-(parent)
            WHERE any(label IN labels(parent) WHERE label IN $allowed_labels)
          AND score >= $score_threshold
          AND ($ontology_id IS NULL OR toInteger(node['ontology_id']) = toInteger($ontology_id))
        RETURN node AS chunk, parent AS parent, score
        ORDER BY score DESC
        LIMIT $k
        """

        t_query_start = time.monotonic()
        result = await self.graph_session.run(
            search_query,
            k=candidate_k,
            query_embedding=query_embedding,
            score_threshold=score_threshold,
            ontology_id=ontology_id,
            allowed_labels=effective_allowed_labels,
        )
        records = await result.data()
        t_query = time.monotonic() - t_query_start
        print(
            f"[RETRIEVAL] step=vector_search chunk_candidates={len(records)} duration_s={t_query:.3f}"
        )

        if not records:
            t_total = time.monotonic() - t_total_start
            print(
                "[RETRIEVAL] step=final results=0 chunks=0 context_chars=0 "
                f"total_duration_s={t_total:.3f}"
            )
            return {
                "query": query,
                "results": [],
                "total": 0,
                "ontology_id": ontology_id,
                "node_scope": node_scope,
            }

        grouped: dict[str, dict[str, Any]] = {}

        query_terms = set(re.findall(r"\w+", query.lower()))
        t_group_start = time.monotonic()

        def _score_node(
            *,
            best_score: float,
            matched_chunk_count: int,
            avg_top_score: float,
            overlap_ratio: float,
            exact_or_fuzzy: float,
            label: str,
        ) -> tuple[float, dict[str, float]]:
            count_norm = min(matched_chunk_count / 5.0, 1.0)
            type_prior = 0.03 if label == "Scene" else 0.01
            breakdown = {
                "vector_best": max(0.0, min(best_score, 1.0)),
                "chunk_coverage": count_norm,
                "top_avg": max(0.0, min(avg_top_score, 1.0)),
                "keyword_overlap": max(0.0, min(overlap_ratio, 1.0)),
                "exact_or_fuzzy": max(0.0, min(exact_or_fuzzy, 1.0)),
                "node_type_prior": type_prior,
            }
            node_score = (
                0.54 * breakdown["vector_best"]
                + 0.15 * breakdown["chunk_coverage"]
                + 0.14 * breakdown["top_avg"]
                + 0.10 * breakdown["keyword_overlap"]
                + 0.05 * breakdown["exact_or_fuzzy"]
                + 0.02 * breakdown["node_type_prior"]
            )
            return max(0.0, min(node_score, 1.0)), breakdown

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
            node_id = (
                _get(parent, "entity_instance_id")
                or _get(parent, "id")
                or _get(parent, "instance_id")
            )
            primary_label = labels_list[0] if labels_list else "node"
            dedup_key = f"{primary_label}::{node_id or _get(parent, 'name') or 'unknown'}"

            entry = grouped.get(dedup_key)
            if entry is None:
                entry = {
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
                    "_chunk_scores": [float(score)],
                    "_overlap_hits": 0,
                    "_exact_or_fuzzy": 0.0,
                }
                grouped[dedup_key] = entry
            else:
                entry["_chunk_scores"].append(float(score))
                if score > entry["score"]:
                    entry["score"] = score
                    entry["context_text"] = chunk_text
                    entry["chunk_id"] = chunk_props.get("chunk_id")
                    entry["chunk_type"] = chunk_props.get("chunk_type")
                    entry["chunk_index"] = chunk_props.get("chunk_index")

            searchable = " ".join(
                [
                    str(entry.get("name") or ""),
                    str(entry.get("alias") or ""),
                    chunk_text,
                ]
            ).lower()
            if query_terms:
                overlap_count = len([term for term in query_terms if term in searchable])
                entry["_overlap_hits"] = max(entry["_overlap_hits"], overlap_count)
                exact = 1.0 if query.lower() in searchable else 0.0
                fuzzy = 1.0 if overlap_count >= max(1, math.ceil(len(query_terms) * 0.7)) else 0.0
                entry["_exact_or_fuzzy"] = max(entry["_exact_or_fuzzy"], max(exact, fuzzy))

        t_group = time.monotonic() - t_group_start
        print(
            f"[RETRIEVAL] step=group_candidates unique_nodes={len(grouped)} duration_s={t_group:.3f}"
        )

        nodes_data: list[dict[str, Any]] = []
        grouped_nodes = list(grouped.values())
        t_score_start = time.monotonic()
        for entry in grouped_nodes:
            chunk_scores = sorted(entry.pop("_chunk_scores"), reverse=True)
            matched_chunk_count = len(chunk_scores)
            avg_top = sum(chunk_scores[: min(3, matched_chunk_count)]) / max(
                1, min(3, matched_chunk_count)
            )
            overlap_ratio = 0.0
            if query_terms:
                overlap_ratio = entry.pop("_overlap_hits") / len(query_terms)
            else:
                entry.pop("_overlap_hits")
            exact_or_fuzzy = entry.pop("_exact_or_fuzzy")
            label = (
                entry.get("labels", ["EntityInstance"])[0]
                if entry.get("labels")
                else "EntityInstance"
            )
            node_score, breakdown = _score_node(
                best_score=float(entry.get("score") or 0.0),
                matched_chunk_count=matched_chunk_count,
                avg_top_score=float(avg_top),
                overlap_ratio=float(overlap_ratio),
                exact_or_fuzzy=float(exact_or_fuzzy),
                label=label,
            )
            entry["chunk_score"] = float(entry.get("score") or 0.0)
            entry["node_score"] = node_score
            entry["importance_index"] = node_score
            entry["matched_chunk_count"] = matched_chunk_count
            entry["score_breakdown"] = breakdown
            nodes_data.append(entry)

        t_score = time.monotonic() - t_score_start
        print(
            f"[RETRIEVAL] step=score_nodes scored_nodes={len(nodes_data)} duration_s={t_score:.3f}"
        )

        nodes_data.sort(
            key=lambda item: float(item.get("node_score") or item.get("score") or 0.0),
            reverse=True,
        )

        rerank_window = max(k, rerank_limit) if rerank_limit is not None else max(k, 20)
        nodes_data = nodes_data[:rerank_window]
        print(
            f"[RETRIEVAL] step=apply_rerank_window rerank_window={rerank_window} "
            f"nodes_in_window={len(nodes_data)}"
        )

        temporal_query = self._is_temporal_query(query_terms)
        scene_neighbor_sets: list[set[str]] = []
        t_neighbors_start = time.monotonic()
        total_neighbors_retrieved = 0

        node_ids = [
            str(node_info.get("node_id"))
            for node_info in nodes_data
            if node_info.get("node_id")
        ]
        neighbors_by_node: dict[str, list[dict[str, Any]]] = {}
        if node_ids:
            try:
                neighbors_by_node = await self._fetch_neighbors_batch(node_ids, neighbor_limit)
            except Exception:
                # Fallback keeps behavior resilient if batch query fails unexpectedly.
                neighbors_by_node = await self._fetch_neighbors_parallel_fallback(
                    node_ids=node_ids,
                    limit=neighbor_limit,
                )

        for node_info in nodes_data:
            node_id = node_info.get("node_id")
            node_key = str(node_id) if node_id is not None else ""
            neighbors = neighbors_by_node.get(node_key, [])
            total_neighbors_retrieved += len(neighbors)
            node_info["_neighbors"] = neighbors
            label = (
                node_info.get("labels", ["EntityInstance"])[0]
                if node_info.get("labels")
                else "EntityInstance"
            )
            if label == "Scene":
                scene_neighbor_sets.append(
                    {str(n.get("node_id")) for n in neighbors if n.get("node_id")}
                )

        t_neighbors = time.monotonic() - t_neighbors_start
        print(
            f"[RETRIEVAL] step=fetch_neighbors nodes_checked={len(nodes_data)} "
            f"neighbors_retrieved={total_neighbors_retrieved} duration_s={t_neighbors:.3f}"
        )

        t_boost_start = time.monotonic()
        for node_info in nodes_data:
            label = (
                node_info.get("labels", ["EntityInstance"])[0]
                if node_info.get("labels")
                else "EntityInstance"
            )
            node_id = node_info.get("node_id")
            neighbors = node_info.get("_neighbors", [])
            scene_occurrence_count = 0
            if node_id:
                scene_occurrence_count = sum(
                    1 for neighbor_set in scene_neighbor_sets if str(node_id) in neighbor_set
                )
            graph_boosts = self._compute_graph_boosts(
                label=label,
                node_id=str(node_id) if node_id is not None else None,
                neighbors=neighbors,
                query_terms=query_terms,
                temporal_query=temporal_query,
                scene_occurrence_count=scene_occurrence_count,
            )
            base_node_score = float(node_info.get("node_score") or node_info.get("score") or 0.0)
            boosted_score = max(
                0.0,
                min(1.0, base_node_score + graph_boosts["graph_total_boost"]),
            )
            score_breakdown = dict(node_info.get("score_breakdown") or {})
            score_breakdown.update(graph_boosts)
            node_info["score_breakdown"] = score_breakdown
            node_info["graph_boost"] = graph_boosts["graph_total_boost"]
            node_info["importance_index"] = boosted_score
            if include_neighbors:
                node_info["neighbors"] = neighbors
            node_info["evidence_bundle"] = self._build_evidence_bundle(node_info, neighbors)
            node_info.pop("_neighbors", None)

        t_boost = time.monotonic() - t_boost_start
        print(
            f"[RETRIEVAL] step=graph_boost_and_evidence nodes_enriched={len(nodes_data)} duration_s={t_boost:.3f}"
        )

        nodes_data.sort(
            key=lambda item: float(item.get("importance_index") or item.get("node_score") or item.get("score") or 0.0),
            reverse=True,
        )

        nodes_data = nodes_data[:k]

        evidence_bundles = [
            node_info.get("evidence_bundle")
            for node_info in nodes_data
            if node_info.get("evidence_bundle") is not None
        ]
        total_context_chars = sum(
            len(str(node_info.get("context_text") or "")) for node_info in nodes_data
        )
        t_total = time.monotonic() - t_total_start
        print(
            "[RETRIEVAL] step=final "
            f"results={len(nodes_data)} chunks={len(nodes_data)} "
            f"evidence_bundles={len(evidence_bundles)} context_chars={total_context_chars} "
            f"total_duration_s={t_total:.3f}"
        )

        out = {
            "query": query,
            "results": nodes_data,
            "total": len(nodes_data),
            "ontology_id": ontology_id,
            "node_scope": node_scope,
            "evidence_bundles": evidence_bundles,
            "debug_stats": {
                "raw_candidates": len(records),
                "after_parent_grouping": len(grouped),
                "after_dedup": len(grouped_nodes),
                "final_k": len(nodes_data),
                "allowed_labels": effective_allowed_labels,
            },
        }

        try:
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
                WHERE (
                        n['entity_instance_id'] = $node_id
                        OR n['id'] = $node_id
                )
                AND any(label IN labels(n) WHERE label IN ['EntityInstance', 'Scene', 'Milestone'])
        MATCH (n)-[r]->(m)
            WHERE any(label IN labels(m) WHERE label IN ['EntityInstance', 'Scene', 'Milestone'])
        RETURN type(r) AS rel_type, 
                     coalesce(m['entity_instance_id'], m['id']) AS node_id,
                     coalesce(m['name'], m['alias'], m['entity_instance_id'], m['id']) AS name,
               CASE
                                     WHEN 'Scene' IN labels(m) THEN 'Scene'
                                     WHEN 'Milestone' IN labels(m) THEN 'Milestone'
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

    async def _fetch_neighbors_batch(
        self, node_ids: list[str], limit: int = 10
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Fetch neighbors for many nodes in one query, grouped by source node ID.

        Args:
            node_ids: Source node IDs
            limit: Max neighbors per source node

        Returns:
            Mapping {source_node_id -> list of neighbor info dicts}
        """
        query = """
        UNWIND $node_ids AS node_id
        MATCH (n)
            WHERE (
                n['entity_instance_id'] = node_id
                OR n['id'] = node_id
            )
            AND any(label IN labels(n) WHERE label IN ['EntityInstance', 'Scene', 'Milestone'])
        MATCH (n)-[r]->(m)
            WHERE any(label IN labels(m) WHERE label IN ['EntityInstance', 'Scene', 'Milestone'])
        WITH node_id, type(r) AS rel_type,
             coalesce(m['entity_instance_id'], m['id']) AS related_node_id,
             coalesce(m['name'], m['alias'], m['entity_instance_id'], m['id']) AS related_name,
             CASE
                 WHEN 'Scene' IN labels(m) THEN 'Scene'
                 WHEN 'Milestone' IN labels(m) THEN 'Milestone'
                 ELSE head(labels(m))
             END AS related_label
        WITH node_id, collect({
            rel_type: rel_type,
            node_id: related_node_id,
            name: related_name,
            label: related_label
        })[..$limit] AS neighbors
        RETURN node_id, neighbors
        """

        result = await self.graph_session.run(
            query, node_ids=node_ids, limit=limit
        )
        records = await result.data()
        out: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            source_node_id = str(record.get("node_id") or "")
            if not source_node_id:
                continue
            neighbors = record.get("neighbors") or []
            out[source_node_id] = [
                {
                    "rel_type": n.get("rel_type"),
                    "node_id": n.get("node_id"),
                    "name": n.get("name"),
                    "label": n.get("label"),
                }
                for n in neighbors
            ]
        return out

    async def _fetch_neighbors_parallel_fallback(
        self, *, node_ids: list[str], limit: int = 10
    ) -> dict[str, list[dict[str, Any]]]:
        semaphore = asyncio.Semaphore(self.neighbor_parallelism_cap)

        async def _one(node_id: str) -> tuple[str, list[dict[str, Any]]]:
            async with semaphore:
                neighbors = await self._fetch_neighbors(node_id, limit)
                return node_id, neighbors

        tasks = [_one(node_id) for node_id in node_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: dict[str, list[dict[str, Any]]] = {}
        had_errors = False
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                had_errors = True
                continue
            node_id, neighbors = result
            out[node_id] = neighbors

        if had_errors:
            # Fall back to fully sequential fetches for unresolved nodes.
            for node_id in node_ids:
                if node_id in out:
                    continue
                out[node_id] = await self._fetch_neighbors(node_id, limit)
        return out

    async def get_context_for_llm(
        self,
        query: str,
        ontology_id: int | None = None,
        k: int = 5,
        score_threshold: float = 0.5,
        node_scope: str = "everything",
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
        t_context_total_start = time.monotonic()
        print(
            f"[RETRIEVAL_CONTEXT] step=start query='{query[:120]}' ontology_id={ontology_id} k={k} node_scope={node_scope}"
        )

        t_context_retrieve_start = time.monotonic()
        results = await self.semantic_search(
            query=query,
            ontology_id=ontology_id,
            k=k,
            score_threshold=score_threshold,
            include_neighbors=True,
            node_scope=node_scope,
        )
        t_context_retrieve = time.monotonic() - t_context_retrieve_start
        print(
            f"[RETRIEVAL_CONTEXT] step=semantic_search_complete nodes={len(results['results'])} duration_s={t_context_retrieve:.3f}"
        )

        if not results["results"]:
            t_context_total = time.monotonic() - t_context_total_start
            print(
                "[RETRIEVAL_CONTEXT] step=final context_items=0 context_chars=0 "
                f"total_duration_s={t_context_total:.3f}"
            )
            return "No relevant information found."

        t_format_start = time.monotonic()
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

        context_text = "\n".join(context_parts)
        t_format = time.monotonic() - t_format_start
        t_context_total = time.monotonic() - t_context_total_start
        print(
            f"[RETRIEVAL_CONTEXT] step=format_context context_items={len(results['results'])} "
            f"context_chars={len(context_text)} duration_s={t_format:.3f}"
        )
        print(
            f"[RETRIEVAL_CONTEXT] step=final context_items={len(results['results'])} "
            f"context_chars={len(context_text)} total_duration_s={t_context_total:.3f}"
        )

        return context_text
