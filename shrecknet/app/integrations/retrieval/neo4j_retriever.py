"""Neo4j graph retriever for Elder pipeline."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Protocol

from neo4j import AsyncSession as AsyncNeo4jSession
from neo4j import GraphDatabase

from app.core.config_store import get_settings
from app.graphrag.embedding_runtime import EmbeddingRuntimeError, get_ready_embedding_runtime
from app.graphrag.retrieval_service import RetrievalService
from app.jobs.elder.schemas import RetrievedChunk

logger = logging.getLogger(__name__)


class GraphRetriever(Protocol):
    """Interface for graph retrieval."""

    async def search(
        self,
        query: str,
        ontology_ids: list[int],
        top_k: int = 10,
        node_scope: str = "everything",
        allowed_labels: list[str] | None = None,
        candidate_limit: int | None = None,
        rerank_limit: int | None = None,
    ) -> list[RetrievedChunk]:
        """
        Search for relevant context in the graph.

        Args:
            query: Search query text
            ontology_ids: List of ontology IDs to scope the search
            top_k: Number of results to return
            node_scope: Node type scope (everything, entity, scene)
            candidate_limit: Max chunk candidates before node-level reranking
            rerank_limit: Max node candidates after reranking

        Returns:
            List of retrieved chunks
        """
        ...

    async def search_aliases(
        self,
        query: str,
        ontology_ids: list[int],
        top_k: int = 10,
    ) -> list[RetrievedChunk]:
        """
        Search for relevant context in the graph.

        Args:
            query: Search query text
            ontology_ids: List of ontology IDs to scope the search
            top_k: Number of results to return

        Returns:
            List of retrieved chunks with simple output
        """
        ...

    async def instance_summaries(
        self, ontology_ids: list[int], max_aliases: int = 8
    ) -> list[dict[str, str]]:
        """Summaries of ontology instances (name + top aliases) for given ontology IDs."""
        ...

    async def list_entities_by_ontology(
        self,
        ontology_id: int,
        *,
        skip: int = 0,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return lightweight entity records for the provided ontology."""
        ...

    async def resolve_source_lineage(self, node_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Return scene/milestone lineage metadata keyed by source node id."""
        ...

    async def expand_timeline_context(
        self,
        *,
        query: str,
        ontology_ids: list[int],
        entity_scores: dict[str, float],
        max_scenes: int = 6,
        max_milestones: int = 6,
    ) -> list[RetrievedChunk]:
        """Return graph-near scene/milestone candidates for retrieved entities."""
        ...


class Neo4jGraphRetriever:
    """Neo4j-based graph retriever using existing GraphRAG service."""

    def __init__(
        self,
        graph_session: AsyncNeo4jSession | None = None,
        *,
        session_factory: Callable[[], AsyncIterator[AsyncNeo4jSession]] | None = None,
    ):
        """
        Initialize retriever.

        Args:
            graph_session: Neo4j async session (shared-session compatibility mode)
            session_factory: Factory that yields a fresh Neo4j session per search call
        """
        if graph_session is None and session_factory is None:
            raise ValueError("Either graph_session or session_factory must be provided")
        self._graph_session = graph_session
        self._session_factory = session_factory
        # Guard concurrent searches only when sharing a single AsyncSession
        self._search_lock = asyncio.Lock()
        self.last_errors: list[str] = []
        self.last_search_stats: list[dict[str, Any]] = []
        self._has_ontology_entity_schema: bool | None = None

    @asynccontextmanager
    async def _acquire_session(self) -> AsyncIterator[AsyncNeo4jSession]:
        if self._session_factory is not None:
            async with self._session_factory() as session:
                yield session
            return
        assert self._graph_session is not None
        yield self._graph_session

    async def search(
        self,
        query: str,
        ontology_ids: list[int],
        top_k: int = 10,
        node_scope: str = "everything",
        allowed_labels: list[str] | None = None,
        candidate_limit: int | None = None,
        rerank_limit: int | None = None,
    ) -> list[RetrievedChunk]:
        """
        Search for relevant context in Neo4j graph.

        Uses the existing semantic search from GraphRAG module.
        Scopes results by ontology IDs and converts to RetrievedChunk format.

        Args:
            query: Search query text
            ontology_ids: List of ontology IDs to filter results
            top_k: Number of results to return
            node_scope: Node type scope (everything, entity, scene)
            candidate_limit: Max chunk candidates before node-level reranking
            rerank_limit: Max node candidates after reranking

        Returns:
            List of retrieved chunks with scores
        """
        chunks: list[RetrievedChunk] = []

        # reset error buffer
        self.last_errors = []
        self.last_search_stats = []
        logger.info(
            "retrieval_start: query='%s' ontologies=%s top_k=%d",
            query,
            ontology_ids or [None],
            top_k,
        )

        # If no ontologies specified, search across all
        search_ontologies = ontology_ids if ontology_ids else [None]

        async def _run_searches(session: AsyncNeo4jSession) -> None:
            retrieval_service = RetrievalService(session)
            for oid in search_ontologies:
                try:
                    results = await retrieval_service.semantic_search(
                        query=query,
                        ontology_id=oid,
                        k=top_k,
                        score_threshold=0.1,
                        include_neighbors=False,
                        node_scope=node_scope,
                        allowed_labels=allowed_labels,
                        candidate_limit=candidate_limit,
                        rerank_limit=rerank_limit,
                    )
                    self.last_search_stats.append(
                        {
                            "ontology_id": oid,
                            "query": query,
                            "debug_stats": results.get("debug_stats") or {},
                        }
                    )
                    nodes = results.get("results", [])
                    for rank, node in enumerate(nodes, start=1):
                        context_text = node.get("context_text") or ""
                        if not context_text:
                            text_parts = []
                            if node.get("text"):
                                text_parts.append(node["text"])
                            if node.get("autogenerated_text"):
                                text_parts.append(node["autogenerated_text"])
                            context_text = (
                                "\n".join(text_parts)
                                if text_parts
                                else node.get("name", "")
                            )

                        chunk = RetrievedChunk(
                            node_id=node.get("node_id", ""),
                            node_label=(
                                node.get("labels", [None])[0]
                                if node.get("labels")
                                else None
                            ),
                            node_name=node.get("name"),
                            node_alias=node.get("alias"),
                            instance_id=node.get("instance_id"),
                            chunk_id=node.get("chunk_id"),
                            chunk_type=node.get("chunk_type"),
                            chunk_index=node.get("chunk_index"),
                            text=context_text,
                            score=node.get("score", 0.0),
                            confidence_pct=round(node.get("score", 0.0) * 100, 2),
                            source=f"ontology_{oid}" if oid else None,
                            properties=node.get("properties") or {},
                            chunk_score=node.get("chunk_score"),
                            node_score=node.get("node_score"),
                            importance_index=node.get("importance_index"),
                            matched_chunk_count=node.get("matched_chunk_count"),
                            score_breakdown=node.get("score_breakdown"),
                            graph_boost=node.get("graph_boost"),
                            evidence_bundle=node.get("evidence_bundle"),
                        )
                        chunks.append(chunk)

                        preview = (context_text or "").strip().replace("\n", " ")
                        if len(preview) > 160:
                            preview = preview[:160] + "…"
                        logger.info(
                            "elder_retrieval_detail subquery='%s' ontology=%s rank=%d score=%.3f chunk_type=%s node=%s instance=%s preview=\"%s\"",
                            query,
                            str(oid),
                            rank,
                            chunk.score,
                            chunk.chunk_type,
                            chunk.node_id,
                            chunk.instance_id,
                            preview,
                        )

                        # print(
                        #     f"[LOGGING]: query: {query} \n"
                        #     + f"[LOGGING]: Ontology_ID: {ontology_ids} \n"
                        #     f"[LOGGING]: Chunk: {chunk.json()} \n"
                        #     f"[LOGGING]: Chunk Name: {chunk.node_name} \n"
                        # )

                    logger.info(
                        "retrieval_done: ontology=%s results=%d",
                        str(oid),
                        len(nodes),
                    )
                except Exception as e:
                    if isinstance(e, EmbeddingRuntimeError):
                        raise
                    msg = f"ontology {oid}: {e}"
                    logger.error(f"Error searching ontology {oid}: {e}")
                    self.last_errors.append(msg)

        async with self._acquire_session() as session:
            # In shared-session mode, serialize to avoid AsyncSession contention.
            if self._session_factory is None:
                async with self._search_lock:
                    await _run_searches(session)
            else:
                await _run_searches(session)

        # Sort by score and take top_k
        chunks.sort(key=lambda c: c.score, reverse=True)
        return chunks[:top_k]

    async def search_aliases(
        self,
        query: str,
        ontology_ids: list[int],
        top_k: int = 10,
    ) -> list[RetrievedChunk]:
        """
        Search for relevant context in Neo4j graph.

        Uses the existing semantic search from GraphRAG module.
        Scopes results by ontology IDs and converts to RetrievedChunk format.

        Args:
            query: Search query text
            ontology_ids: List of ontology IDs to filter results
            top_k: Number of results to return

        Returns:
            List of retrieved chunks with scores
        """
        chunks: list[RetrievedChunk] = []

        # reset error buffer
        self.last_errors = []
        logger.info(
            "retrieval_start: query='%s' ontologies=%s top_k=%d",
            query,
            ontology_ids or [None],
            top_k,
        )

        # If no ontologies specified, search across all
        search_ontologies = ontology_ids if ontology_ids else [None]

        async def _run_searches(session: AsyncNeo4jSession) -> None:
            retrieval_service = RetrievalService(session)
            for oid in search_ontologies:
                try:
                    results = await retrieval_service.semantic_search(
                        query=query,
                        ontology_id=oid,
                        k=top_k,
                        score_threshold=0.1,
                        include_neighbors=False,
                    )
                    nodes = results.get("results", [])
                    for rank, node in enumerate(nodes, start=1):
                        # context_text = node.get("context_text") or ""
                        # if not context_text:
                        #     text_parts = []
                        #     if node.get("text"):
                        #         text_parts.append(node["text"])
                        #     if node.get("autogenerated_text"):
                        #         text_parts.append(node["autogenerated_text"])
                        #     context_text = (
                        #         "\n".join(text_parts)
                        #         if text_parts
                        #         else node.get("name", "")
                        #     )

                        chunk = RetrievedChunk(
                            node_id=node.get("node_id", ""),
                            node_label="",
                            node_name="",
                            node_alias=node.get("alias"),
                            instance_id=node.get("instance_id"),
                            chunk_id=node.get("chunk_id"),
                            chunk_type=node.get("chunk_type"),
                            chunk_index=node.get("chunk_index"),
                            text="",
                            score=node.get("score", 0.0),
                            confidence_pct=round(node.get("score", 0.0) * 100, 2),
                            source=f"ontology_{oid}" if oid else None,
                            properties={},
                        )
                        chunks.append(chunk)

                        # preview = (context_text or "").strip().replace("\n", " ")
                        # if len(preview) > 160:
                        #     preview = preview[:160] + "…"
                        logger.info(
                            "elder_retrieval_detail subquery='%s' ontology=%s rank=%d score=%.3f chunk_type=%s node=%s instance=%s",
                            query,
                            str(oid),
                            rank,
                            chunk.score,
                            chunk.chunk_type,
                            chunk.node_id,
                            chunk.instance_id,
                            # preview,
                        )

                        # print(
                        #     f"[LOGGING]: query: {query} \n"
                        #     + f"[LOGGING]: Ontology_ID: {ontology_ids} \n"
                        #     f"[LOGGING]: Chunk: {chunk.json()} \n"
                        #     f"[LOGGING]: Chunk Name: {chunk.node_name} \n"
                        # )

                    logger.info(
                        "retrieval_done: ontology=%s results=%d",
                        str(oid),
                        len(nodes),
                    )
                except Exception as e:
                    msg = f"ontology {oid}: {e}"
                    logger.error(f"Error searching ontology {oid}: {e}")
                    self.last_errors.append(msg)

        async with self._acquire_session() as session:
            # In shared-session mode, serialize to avoid AsyncSession contention.
            if self._session_factory is None:
                async with self._search_lock:
                    await _run_searches(session)
            else:
                await _run_searches(session)

        # Sort by score and take top_k
        chunks.sort(key=lambda c: c.score, reverse=True)
        return chunks[:top_k]

    async def resolve_source_lineage(self, node_ids: list[str]) -> dict[str, dict[str, Any]]:
        unique_ids = [str(node_id).strip() for node_id in node_ids if str(node_id).strip()]
        if not unique_ids:
            return {}

        query = """
        MATCH (node)
        WHERE (node:Scene OR node:Milestone) AND node.id IN $node_ids
        OPTIONAL MATCH (node)-[:DERIVED_FROM]->(entity:EntityInstance)
        RETURN node.id AS node_id,
               CASE
                   WHEN 'Scene' IN labels(node) THEN 'scene'
                   WHEN 'Milestone' IN labels(node) THEN 'milestone'
                   ELSE null
               END AS node_type,
               CASE
                   WHEN 'Scene' IN labels(node) THEN node.id
                   ELSE node.scene_id
               END AS scene_id,
               entity.entity_instance_id AS source_entity_instance_id
        """

        async with self._acquire_session() as session:
            retrieval_service = RetrievalService(session)
            if self._session_factory is None:
                async with self._search_lock:
                    result = await retrieval_service.graph_session.run(query, node_ids=unique_ids)
                    rows = await result.data()
            else:
                result = await retrieval_service.graph_session.run(query, node_ids=unique_ids)
                rows = await result.data()

        return {
            str(row.get("node_id")): {
                "node_type": row.get("node_type"),
                "scene_id": row.get("scene_id"),
                "source_entity_instance_id": row.get("source_entity_instance_id"),
            }
            for row in rows
            if row.get("node_id")
        }

    async def expand_timeline_context(
        self,
        *,
        query: str,
        ontology_ids: list[int],
        entity_scores: dict[str, float],
        max_scenes: int = 6,
        max_milestones: int = 6,
    ) -> list[RetrievedChunk]:
        entity_ids = [entity_id for entity_id in entity_scores if entity_id]
        if not entity_ids:
            return []

        scenes_query = """
        UNWIND $entity_ids AS entity_id
        MATCH (scene:Scene)
        WHERE scene.ontology_id IN $ontology_ids
          AND (
            EXISTS {
                MATCH (scene)-[:DERIVED_FROM]->(:EntityInstance {entity_instance_id: entity_id})
            }
            OR EXISTS {
                MATCH (scene)-[:RELATES_TO]->(:EntityInstance {entity_instance_id: entity_id})
            }
            OR EXISTS {
                MATCH (scene)-[:CONTAINS]->(:Milestone)-[:DERIVED_FROM]->(:EntityInstance {entity_instance_id: entity_id})
            }
            OR EXISTS {
                MATCH (scene)-[:CONTAINS]->(:Milestone)-[:RELATES_TO]->(:EntityInstance {entity_instance_id: entity_id})
            }
          )
        OPTIONAL MATCH (scene)-[:DERIVED_FROM]->(derived:EntityInstance)
        WITH scene,
             collect(DISTINCT entity_id) AS matched_entity_ids,
             head(collect(DISTINCT derived.entity_instance_id)) AS derived_from_entity_id
        RETURN scene AS node,
               matched_entity_ids,
               derived_from_entity_id
        """
        milestones_query = """
        UNWIND $entity_ids AS entity_id
        MATCH (scene:Scene)-[:CONTAINS]->(milestone:Milestone)
        WHERE scene.ontology_id IN $ontology_ids
          AND (
            EXISTS {
                MATCH (milestone)-[:DERIVED_FROM]->(:EntityInstance {entity_instance_id: entity_id})
            }
            OR EXISTS {
                MATCH (milestone)-[:RELATES_TO]->(:EntityInstance {entity_instance_id: entity_id})
            }
          )
        OPTIONAL MATCH (milestone)-[:DERIVED_FROM]->(derived:EntityInstance)
        WITH scene,
             milestone,
             collect(DISTINCT entity_id) AS matched_entity_ids,
             head(collect(DISTINCT derived.entity_instance_id)) AS derived_from_entity_id
        RETURN milestone AS node,
               scene.id AS scene_id,
               matched_entity_ids,
               derived_from_entity_id
        """

        async with self._acquire_session() as session:
            retrieval_service = RetrievalService(session)
            if self._session_factory is None:
                async with self._search_lock:
                    scene_result = await retrieval_service.graph_session.run(
                        scenes_query,
                        entity_ids=entity_ids,
                        ontology_ids=ontology_ids,
                    )
                    scene_rows = await scene_result.data()
                    milestone_result = await retrieval_service.graph_session.run(
                        milestones_query,
                        entity_ids=entity_ids,
                        ontology_ids=ontology_ids,
                    )
                    milestone_rows = await milestone_result.data()
            else:
                scene_result = await retrieval_service.graph_session.run(
                    scenes_query,
                    entity_ids=entity_ids,
                    ontology_ids=ontology_ids,
                )
                scene_rows = await scene_result.data()
                milestone_result = await retrieval_service.graph_session.run(
                    milestones_query,
                    entity_ids=entity_ids,
                    ontology_ids=ontology_ids,
                )
                milestone_rows = await milestone_result.data()

        query_tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", str(query or "").lower())
            if len(token) >= 4
        }

        def _candidate_score(
            *,
            matched_entity_ids: list[str],
            text: str,
            node_type: str,
            source_entity_instance_id: str | None,
        ) -> float:
            base = max((float(entity_scores.get(entity_id) or 0.0) for entity_id in matched_entity_ids), default=0.35)
            text_tokens = {
                token
                for token in re.findall(r"[a-z0-9]+", str(text or "").lower())
                if len(token) >= 4
            }
            overlap = len(query_tokens & text_tokens) / max(1, len(query_tokens)) if query_tokens else 0.0
            lexical_bonus = min(0.08, overlap * 0.12)
            type_bonus = 0.06 if node_type == "scene" else 0.04
            lineage_bonus = 0.04 if source_entity_instance_id and source_entity_instance_id in matched_entity_ids else 0.0
            return round(min(0.99, base + lexical_bonus + type_bonus + lineage_bonus), 6)

        def _build_chunk(
            *,
            row: dict[str, Any],
            node_type: str,
        ) -> RetrievedChunk | None:
            node = row.get("node")
            if not node:
                return None
            props = dict(node)
            node_id = str(props.get("id") or "").strip()
            if not node_id:
                return None
            name = str(props.get("name") or node_id)
            description = str(props.get("description") or "").strip()
            text = f"{node_type.title()}: {name}\n{description}".strip()
            matched_entity_ids = [str(item) for item in row.get("matched_entity_ids") or [] if str(item).strip()]
            source_entity_instance_id = row.get("derived_from_entity_id")
            score = _candidate_score(
                matched_entity_ids=matched_entity_ids,
                text=text,
                node_type=node_type,
                source_entity_instance_id=str(source_entity_instance_id) if source_entity_instance_id else None,
            )
            return RetrievedChunk(
                node_id=node_id,
                node_label="Scene" if node_type == "scene" else "Milestone",
                node_name=name,
                instance_id=props.get("instance_id"),
                chunk_id=f"{node_id}-expanded",
                chunk_type="scene_main" if node_type == "scene" else "milestone_main",
                text=text,
                score=score,
                confidence_pct=round(score * 100, 2),
                source="timeline_expansion",
                properties={
                    "scene_id": row.get("scene_id") or node_id if node_type == "scene" else row.get("scene_id"),
                    "source_entity_instance_id": source_entity_instance_id,
                    "matched_entity_ids": matched_entity_ids,
                    "expanded_from_graph": True,
                },
            )

        scene_chunks = [_build_chunk(row=row, node_type="scene") for row in scene_rows]
        milestone_chunks = [_build_chunk(row=row, node_type="milestone") for row in milestone_rows]
        chunks = [chunk for chunk in scene_chunks if chunk is not None]
        chunks.extend(chunk for chunk in milestone_chunks if chunk is not None)
        chunks.sort(key=lambda chunk: chunk.score, reverse=True)

        selected: list[RetrievedChunk] = []
        scene_count = 0
        milestone_count = 0
        seen_node_ids: set[str] = set()
        for chunk in chunks:
            if chunk.node_id in seen_node_ids:
                continue
            if chunk.node_label == "Scene":
                if scene_count >= max_scenes:
                    continue
                scene_count += 1
            elif chunk.node_label == "Milestone":
                if milestone_count >= max_milestones:
                    continue
                milestone_count += 1
            seen_node_ids.add(chunk.node_id)
            selected.append(chunk)
        return selected

    async def instance_summaries(
        self, ontology_ids: list[int], max_aliases: int = 8
    ) -> list[dict[str, str]]:
        """Return lightweight summaries for instances in given ontologies.

        Summary includes instance name and up to N top aliases to guide prompts.
        """
        summaries: list[dict[str, str]] = []
        if not ontology_ids:
            return summaries
        query = """
        MATCH (i:OntologyInstance)
        WHERE i.ontology_id IN $ontology_ids
        OPTIONAL MATCH (i)-[:HAS_ENTITY]->(e:EntityInstance)
        WITH i, collect(e.alias)[..$max_aliases] AS aliases, count(e) AS entity_count
        RETURN i.instance_id AS instance_id, coalesce(i.name,'(unnamed)') AS name,
               aliases, entity_count
        ORDER BY name ASC
        LIMIT 50
        """
        try:
            async with self._acquire_session() as session:
                retrieval_service = RetrievalService(session)
                if self._session_factory is None:
                    async with self._search_lock:
                        result = await retrieval_service.graph_session.run(
                            query, ontology_ids=ontology_ids, max_aliases=max_aliases
                        )
                        records = await result.data()
                else:
                    result = await retrieval_service.graph_session.run(
                        query, ontology_ids=ontology_ids, max_aliases=max_aliases
                    )
                    records = await result.data()
            for r in records:
                alias_list = [a for a in (r.get("aliases") or []) if a]
                hint = f"aliases: {', '.join(alias_list)} | entities: {r.get('entity_count',0)}"
                summaries.append(
                    {
                        "instance_id": r.get("instance_id", ""),
                        "name": r.get("name", "(unnamed)"),
                        "hint": hint,
                    }
                )
        except Exception as e:
            logger.error(f"Error fetching instance summaries: {e}")
        return summaries

    async def list_entities_by_ontology(
        self,
        ontology_id: int,
        *,
        skip: int = 0,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Stream entity aliases for a single ontology in deterministic batches."""
        if not await self._ensure_ontology_entity_schema():
            return []

        query = """
        MATCH (inst:OntologyInstance)-[:HAS_ENTITY]->(entity:EntityInstance)
          WHERE toInteger(coalesce(inst['ontology_id'], -1)) = toInteger($ontology_id)
              OR toInteger(coalesce(entity['ontology_id'], -1)) = toInteger($ontology_id)
        RETURN coalesce(entity['entity_instance_id'], elementId(entity)) AS node_id,
               coalesce(entity['alias'], entity['name'], entity['entity_instance_id'], elementId(entity)) AS alias,
               head(labels(entity)) AS ontology
        ORDER BY alias, node_id
        SKIP $skip
        LIMIT $limit
        """
        try:
            async with self._acquire_session() as session:
                retrieval_service = RetrievalService(session)
                if self._session_factory is None:
                    async with self._search_lock:
                        result = await retrieval_service.graph_session.run(
                            query,
                            ontology_id=ontology_id,
                            skip=skip,
                            limit=limit,
                        )
                        records = await result.data()
                else:
                    result = await retrieval_service.graph_session.run(
                        query,
                        ontology_id=ontology_id,
                        skip=skip,
                        limit=limit,
                    )
                    records = await result.data()
        except Exception as e:
            msg = f"ontology {ontology_id}: {e}"
            logger.error("Error fetching entities for ontology %s: %s", ontology_id, e)
            self.last_errors.append(msg)
            return []

        entities: list[dict[str, Any]] = []
        for record in records:
            node_id = record.get("node_id")
            alias = record.get("alias")
            ontology_label = record.get("ontology") or f"ontology_{ontology_id}"
            if not node_id or not alias:
                continue
            entities.append(
                {
                    "node_id": node_id,
                    "alias": alias,
                    "ontology": ontology_label,
                }
            )

        return entities

    async def _ensure_ontology_entity_schema(self) -> bool:
        """Check if ontology entity labels/relationship exist before running strict label query."""
        if self._has_ontology_entity_schema is not None:
            return self._has_ontology_entity_schema

        labels_query = "CALL db.labels() YIELD label RETURN collect(label) AS labels"
        rels_query = "CALL db.relationshipTypes() YIELD relationshipType RETURN collect(relationshipType) AS rels"

        try:
            async with self._acquire_session() as session:
                retrieval_service = RetrievalService(session)
                if self._session_factory is None:
                    async with self._search_lock:
                        labels_res = await retrieval_service.graph_session.run(labels_query)
                        labels_row = await labels_res.single()
                        rels_res = await retrieval_service.graph_session.run(rels_query)
                        rels_row = await rels_res.single()
                else:
                    labels_res = await retrieval_service.graph_session.run(labels_query)
                    labels_row = await labels_res.single()
                    rels_res = await retrieval_service.graph_session.run(rels_query)
                    rels_row = await rels_res.single()

            labels = set(labels_row.get("labels") or []) if labels_row else set()
            rels = set(rels_row.get("rels") or []) if rels_row else set()
            self._has_ontology_entity_schema = (
                "OntologyInstance" in labels
                and "EntityInstance" in labels
                and "HAS_ENTITY" in rels
            )
        except Exception:
            # If schema introspection is unavailable, don't block retrieval.
            self._has_ontology_entity_schema = True

        return self._has_ontology_entity_schema


class HybridNeo4jGraphRetriever(Neo4jGraphRetriever):
    """Elder retriever using hybrid chunk anchors plus Shrecknet temporal expansion."""

    vector_index_name = "entity_chunk_vec_idx"
    fulltext_index_name = "entity_chunk_fulltext_idx"

    def __init__(
        self,
        graph_session: AsyncNeo4jSession | None = None,
        *,
        session_factory: Callable[[], AsyncIterator[AsyncNeo4jSession]] | None = None,
    ):
        super().__init__(graph_session, session_factory=session_factory)
        self._official_hybrid_available = self._detect_official_hybrid_retriever()
        self._sync_driver = None
        if self._official_hybrid_available:
            settings = get_settings()
            self._sync_driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
                max_connection_lifetime=3600,
            )

    @staticmethod
    def _detect_official_hybrid_retriever() -> bool:
        try:
            from neo4j_graphrag.retrievers import HybridCypherRetriever  # noqa: F401

            return True
        except Exception:
            logger.warning(
                "elder_hybrid_official_retriever_unavailable fallback=async_driver_hybrid"
            )
            return False

    @staticmethod
    def infer_temporal_mode(query: str) -> str:
        text = f" {str(query or '').lower()} "
        if any(token in text for token in (" before ", " prior to ", " earlier than ")):
            return "before"
        if any(token in text for token in (" after ", " later than ", " following ")):
            return "after"
        if any(token in text for token in (" as of ", " at that point ", " by then ")):
            return "as_of"
        if any(token in text for token in (" relationship ", " relation ", " connected ", " why ")):
            return "relationship_explanation"
        if any(token in text for token in (" history ", " timeline ", " chronolog", " sequence ", " happened ")):
            return "history_timeline"
        return "current_state"

    @staticmethod
    def _fulltext_query(query: str) -> str:
        terms = [
            token
            for token in re.findall(r"[A-Za-z0-9]+", str(query or "").lower())
            if len(token) >= 2
        ]
        return " OR ".join(terms[:12]) or str(query or "").strip()

    @staticmethod
    def _node_id(props: dict[str, Any]) -> str:
        return str(
            props.get("entity_instance_id")
            or props.get("id")
            or props.get("instance_id")
            or ""
        )

    @staticmethod
    def _primary_label(labels: list[str]) -> str:
        for label in ("EntityInstance", "Scene", "Milestone"):
            if label in labels:
                return label
        return labels[0] if labels else "Node"

    @staticmethod
    def _as_props(node: Any) -> dict[str, Any]:
        try:
            return dict(node)
        except Exception:
            return {}

    @staticmethod
    def _as_labels(node: Any) -> list[str]:
        try:
            return list(node.labels)
        except Exception:
            return []

    async def search(
        self,
        query: str,
        ontology_ids: list[int],
        top_k: int = 10,
        node_scope: str = "everything",
        allowed_labels: list[str] | None = None,
        candidate_limit: int | None = None,
        rerank_limit: int | None = None,
    ) -> list[RetrievedChunk]:
        started = time.monotonic()
        self.last_errors = []
        self.last_search_stats = []
        labels = allowed_labels or ["EntityInstance", "Scene", "Milestone"]
        search_ontologies = ontology_ids if ontology_ids else [None]
        all_chunks: list[RetrievedChunk] = []
        mode = self.infer_temporal_mode(query)

        async with self._acquire_session() as session:
            if self._session_factory is None:
                async with self._search_lock:
                    all_chunks = await self._search_with_session(
                        session=session,
                        query=query,
                        search_ontologies=search_ontologies,
                        top_k=top_k,
                        allowed_labels=labels,
                        candidate_limit=candidate_limit,
                        rerank_limit=rerank_limit,
                        temporal_mode=mode,
                    )
            else:
                all_chunks = await self._search_with_session(
                    session=session,
                    query=query,
                    search_ontologies=search_ontologies,
                    top_k=top_k,
                    allowed_labels=labels,
                    candidate_limit=candidate_limit,
                    rerank_limit=rerank_limit,
                    temporal_mode=mode,
                )

        all_chunks.sort(key=lambda chunk: float(chunk.importance_index or chunk.score), reverse=True)
        duration_ms = round((time.monotonic() - started) * 1000, 2)
        logger.info(
            "elder_hybrid_retrieval_done query='%s' ontologies=%s mode=%s chunks=%d duration_ms=%.2f official_available=%s",
            query[:160],
            ontology_ids or [None],
            mode,
            len(all_chunks),
            duration_ms,
            self._official_hybrid_available,
        )
        return all_chunks[:top_k]

    async def _search_with_session(
        self,
        *,
        session: AsyncNeo4jSession,
        query: str,
        search_ontologies: list[int | None],
        top_k: int,
        allowed_labels: list[str],
        candidate_limit: int | None,
        rerank_limit: int | None,
        temporal_mode: str,
    ) -> list[RetrievedChunk]:
        try:
            runtime = await get_ready_embedding_runtime()
            query_embedding = await runtime.embed_query(query, request_id=f"elder-hybrid-{time.monotonic_ns()}")
        except Exception as exc:
            if isinstance(exc, EmbeddingRuntimeError):
                raise
            logger.error("elder_hybrid_query_embedding_failed error=%s", exc)
            raise EmbeddingRuntimeError(str(exc)) from exc

        chunks: list[RetrievedChunk] = []
        for ontology_id in search_ontologies:
            candidate_k = candidate_limit or max(top_k * 6, top_k)
            anchors = await self._hybrid_anchor_search(
                session=session,
                query=query,
                query_embedding=query_embedding,
                ontology_id=ontology_id,
                allowed_labels=allowed_labels,
                candidate_k=candidate_k,
                rerank_limit=rerank_limit or max(top_k * 3, top_k),
            )
            expanded = await self._expand_anchor_context(
                session=session,
                anchors=anchors,
                ontology_id=ontology_id,
                temporal_mode=temporal_mode,
            )
            merged = self._merge_anchor_and_expanded_chunks(
                anchors=anchors,
                expanded=expanded,
                ontology_id=ontology_id,
                temporal_mode=temporal_mode,
            )
            self.last_search_stats.append(
                {
                    "ontology_id": ontology_id,
                    "query": query,
                    "debug_stats": {
                        "retrieval_mode": "hybrid_cypher",
                        "temporal_mode": temporal_mode,
                        "raw_candidates": sum(int(a.get("matched_chunk_count") or 1) for a in anchors),
                        "after_parent_grouping": len(anchors),
                        "after_dedup": len(anchors),
                        "expanded_context": len(expanded),
                        "final_k": min(top_k, len(merged)),
                        "allowed_labels": allowed_labels,
                    },
                }
            )
            chunks.extend(merged)
        return chunks

    async def _hybrid_anchor_search(
        self,
        *,
        session: AsyncNeo4jSession,
        query: str,
        query_embedding: list[float],
        ontology_id: int | None,
        allowed_labels: list[str],
        candidate_k: int,
        rerank_limit: int,
    ) -> list[dict[str, Any]]:
        vector_query = f"""
        CALL db.index.vector.queryNodes('{self.vector_index_name}', $candidate_k, $query_embedding)
        YIELD node, score
        MATCH (parent)-[:HAS_CHUNK]->(node)
        WHERE any(label IN labels(parent) WHERE label IN $allowed_labels)
          AND ($ontology_id IS NULL OR toInteger(node.ontology_id) = toInteger($ontology_id))
        RETURN node AS chunk, parent AS parent, score AS score, 'vector' AS source
        ORDER BY score DESC
        LIMIT $candidate_k
        """
        fulltext_query = f"""
        CALL db.index.fulltext.queryNodes('{self.fulltext_index_name}', $fulltext_query)
        YIELD node, score
        MATCH (parent)-[:HAS_CHUNK]->(node)
        WHERE any(label IN labels(parent) WHERE label IN $allowed_labels)
          AND ($ontology_id IS NULL OR toInteger(node.ontology_id) = toInteger($ontology_id))
        RETURN node AS chunk, parent AS parent, score AS score, 'fulltext' AS source
        ORDER BY score DESC
        LIMIT $candidate_k
        """
        if self._sync_driver is not None:
            try:
                return await self._official_hybrid_anchor_search(
                    query=query,
                    query_embedding=query_embedding,
                    ontology_id=ontology_id,
                    allowed_labels=allowed_labels,
                    candidate_k=candidate_k,
                    rerank_limit=rerank_limit,
                )
            except Exception as exc:
                logger.warning(
                    "elder_hybrid_official_anchor_failed ontology=%s fallback=async_driver_hybrid error=%s",
                    ontology_id,
                    exc,
                )

        rows: list[dict[str, Any]] = []
        try:
            result = await session.run(
                vector_query,
                candidate_k=candidate_k,
                query_embedding=query_embedding,
                ontology_id=ontology_id,
                allowed_labels=allowed_labels,
            )
            rows.extend(await result.data())
        except Exception as exc:
            logger.error("elder_hybrid_vector_anchor_failed ontology=%s error=%s", ontology_id, exc)
            raise
        try:
            result = await session.run(
                fulltext_query,
                candidate_k=candidate_k,
                fulltext_query=self._fulltext_query(query),
                ontology_id=ontology_id,
                allowed_labels=allowed_labels,
            )
            rows.extend(await result.data())
        except Exception as exc:
            logger.error("elder_hybrid_fulltext_anchor_failed ontology=%s error=%s", ontology_id, exc)
            raise

        max_fulltext = max(
            [float(row.get("score") or 0.0) for row in rows if row.get("source") == "fulltext"],
            default=1.0,
        )
        grouped: dict[str, dict[str, Any]] = {}
        query_terms = {
            token for token in re.findall(r"[a-z0-9]+", str(query or "").lower()) if token
        }
        for row in rows:
            parent = row.get("parent")
            chunk = row.get("chunk")
            parent_props = self._as_props(parent)
            chunk_props = self._as_props(chunk)
            labels = self._as_labels(parent)
            node_id = self._node_id(parent_props)
            if not node_id:
                continue
            label = self._primary_label(labels)
            source = str(row.get("source") or "hybrid")
            raw_score = float(row.get("score") or 0.0)
            score = raw_score if source == "vector" else min(1.0, raw_score / max(1.0, max_fulltext))
            text = str(chunk_props.get("text_chunk") or parent_props.get("description") or parent_props.get("text") or "")
            searchable = " ".join(
                [
                    str(parent_props.get("name") or ""),
                    str(parent_props.get("alias") or ""),
                    text,
                ]
            ).lower()
            overlap = len([term for term in query_terms if term in searchable]) / max(1, len(query_terms)) if query_terms else 0.0
            key = f"{label}::{node_id}"
            existing = grouped.get(key)
            if existing is None:
                existing = {
                    "node_id": node_id,
                    "node_label": label,
                    "node_name": parent_props.get("name"),
                    "node_alias": parent_props.get("alias"),
                    "instance_id": parent_props.get("instance_id"),
                    "properties": parent_props,
                    "context_text": text,
                    "chunk_id": chunk_props.get("chunk_id"),
                    "chunk_type": chunk_props.get("chunk_type"),
                    "chunk_index": chunk_props.get("chunk_index"),
                    "vector_score": 0.0,
                    "fulltext_score": 0.0,
                    "keyword_overlap": overlap,
                    "matched_chunk_count": 0,
                }
                grouped[key] = existing
            existing["matched_chunk_count"] = int(existing.get("matched_chunk_count") or 0) + 1
            existing["keyword_overlap"] = max(float(existing.get("keyword_overlap") or 0.0), overlap)
            if source == "vector":
                existing["vector_score"] = max(float(existing.get("vector_score") or 0.0), score)
            else:
                existing["fulltext_score"] = max(float(existing.get("fulltext_score") or 0.0), score)
            combined = (
                0.58 * float(existing.get("vector_score") or 0.0)
                + 0.30 * float(existing.get("fulltext_score") or 0.0)
                + 0.12 * float(existing.get("keyword_overlap") or 0.0)
            )
            if combined >= float(existing.get("score") or 0.0):
                existing["score"] = min(1.0, combined)
                existing["context_text"] = text
                existing["chunk_id"] = chunk_props.get("chunk_id")
                existing["chunk_type"] = chunk_props.get("chunk_type")
                existing["chunk_index"] = chunk_props.get("chunk_index")

        anchors = sorted(
            grouped.values(),
            key=lambda item: float(item.get("score") or 0.0),
            reverse=True,
        )[:rerank_limit]
        return anchors

    async def _official_hybrid_anchor_search(
        self,
        *,
        query: str,
        query_embedding: list[float],
        ontology_id: int | None,
        allowed_labels: list[str],
        candidate_k: int,
        rerank_limit: int,
    ) -> list[dict[str, Any]]:
        from neo4j_graphrag.retrievers import HybridCypherRetriever

        settings = get_settings()
        retrieval_query = """
        MATCH (parent)-[:HAS_CHUNK]->(node)
        WHERE any(label IN labels(parent) WHERE label IN $allowed_labels)
          AND ($ontology_id IS NULL OR toInteger(node.ontology_id) = toInteger($ontology_id))
        RETURN node AS chunk, parent AS parent, score AS score, 'hybrid_cypher' AS source
        """

        def _run() -> list[dict[str, Any]]:
            retriever = HybridCypherRetriever(
                self._sync_driver,
                self.vector_index_name,
                self.fulltext_index_name,
                retrieval_query,
                neo4j_database=settings.neo4j_database,
            )
            result = retriever.get_search_results(
                query_text=self._fulltext_query(query),
                query_vector=query_embedding,
                top_k=candidate_k,
                effective_search_ratio=1,
                query_params={
                    "ontology_id": ontology_id,
                    "allowed_labels": allowed_labels,
                },
            )
            out: list[dict[str, Any]] = []
            for record in result.records:
                try:
                    out.append(record.data())
                except Exception:
                    out.append(dict(record))
            return out

        rows = await asyncio.get_running_loop().run_in_executor(None, _run)
        return self._group_hybrid_rows(
            rows=rows,
            query=query,
            rerank_limit=rerank_limit,
        )

    def _group_hybrid_rows(
        self,
        *,
        rows: list[dict[str, Any]],
        query: str,
        rerank_limit: int,
    ) -> list[dict[str, Any]]:
        max_fulltext = max(
            [float(row.get("score") or 0.0) for row in rows if row.get("source") == "fulltext"],
            default=1.0,
        )
        grouped: dict[str, dict[str, Any]] = {}
        query_terms = {
            token for token in re.findall(r"[a-z0-9]+", str(query or "").lower()) if token
        }
        for row in rows:
            parent = row.get("parent")
            chunk = row.get("chunk")
            parent_props = self._as_props(parent)
            chunk_props = self._as_props(chunk)
            labels = self._as_labels(parent)
            node_id = self._node_id(parent_props)
            if not node_id:
                continue
            label = self._primary_label(labels)
            source = str(row.get("source") or "hybrid")
            raw_score = float(row.get("score") or 0.0)
            score = raw_score if source in {"vector", "hybrid_cypher"} else min(1.0, raw_score / max(1.0, max_fulltext))
            text = str(chunk_props.get("text_chunk") or parent_props.get("description") or parent_props.get("text") or "")
            searchable = " ".join(
                [
                    str(parent_props.get("name") or ""),
                    str(parent_props.get("alias") or ""),
                    text,
                ]
            ).lower()
            overlap = len([term for term in query_terms if term in searchable]) / max(1, len(query_terms)) if query_terms else 0.0
            key = f"{label}::{node_id}"
            existing = grouped.get(key)
            if existing is None:
                existing = {
                    "node_id": node_id,
                    "node_label": label,
                    "node_name": parent_props.get("name"),
                    "node_alias": parent_props.get("alias"),
                    "instance_id": parent_props.get("instance_id"),
                    "properties": parent_props,
                    "context_text": text,
                    "chunk_id": chunk_props.get("chunk_id"),
                    "chunk_type": chunk_props.get("chunk_type"),
                    "chunk_index": chunk_props.get("chunk_index"),
                    "vector_score": 0.0,
                    "fulltext_score": 0.0,
                    "keyword_overlap": overlap,
                    "matched_chunk_count": 0,
                }
                grouped[key] = existing
            existing["matched_chunk_count"] = int(existing.get("matched_chunk_count") or 0) + 1
            existing["keyword_overlap"] = max(float(existing.get("keyword_overlap") or 0.0), overlap)
            if source in {"vector", "hybrid_cypher"}:
                existing["vector_score"] = max(float(existing.get("vector_score") or 0.0), score)
            else:
                existing["fulltext_score"] = max(float(existing.get("fulltext_score") or 0.0), score)
            combined = (
                0.58 * float(existing.get("vector_score") or 0.0)
                + 0.30 * float(existing.get("fulltext_score") or 0.0)
                + 0.12 * float(existing.get("keyword_overlap") or 0.0)
            )
            if combined >= float(existing.get("score") or 0.0):
                existing["score"] = min(1.0, combined)
                existing["context_text"] = text
                existing["chunk_id"] = chunk_props.get("chunk_id")
                existing["chunk_type"] = chunk_props.get("chunk_type")
                existing["chunk_index"] = chunk_props.get("chunk_index")

        return sorted(
            grouped.values(),
            key=lambda item: float(item.get("score") or 0.0),
            reverse=True,
        )[:rerank_limit]

    async def _expand_anchor_context(
        self,
        *,
        session: AsyncNeo4jSession,
        anchors: list[dict[str, Any]],
        ontology_id: int | None,
        temporal_mode: str,
    ) -> list[dict[str, Any]]:
        anchor_payload = [
            {
                "id": anchor["node_id"],
                "label": anchor["node_label"],
                "score": float(anchor.get("score") or 0.0),
            }
            for anchor in anchors
            if anchor.get("node_id") and anchor.get("node_label")
        ]
        if not anchor_payload:
            return []

        order_direction = "DESC" if temporal_mode == "current_state" else "ASC"
        query = f"""
        UNWIND $anchors AS anchor
        MATCH (a)
        WHERE (
            (anchor.label = 'EntityInstance' AND a:EntityInstance AND a.entity_instance_id = anchor.id)
            OR (anchor.label = 'Scene' AND a:Scene AND a.id = anchor.id)
            OR (anchor.label = 'Milestone' AND a:Milestone AND a.id = anchor.id)
        )
        CALL {{
            WITH a, anchor
            OPTIONAL MATCH (scene:Scene)-[scene_rel:RELATES_TO|DERIVED_FROM]->(a)
            WHERE a:EntityInstance
            RETURN scene AS node, 'Scene' AS node_label, type(scene_rel) AS relation, anchor.score AS anchor_score
            UNION
            WITH a, anchor
            OPTIONAL MATCH (scene:Scene)-[:CONTAINS]->(milestone:Milestone)-[mrel:RELATES_TO|DERIVED_FROM]->(a)
            WHERE a:EntityInstance
            RETURN milestone AS node, 'Milestone' AS node_label, type(mrel) AS relation, anchor.score AS anchor_score
            UNION
            WITH a, anchor
            OPTIONAL MATCH (a:Scene)-[:CONTAINS]->(milestone:Milestone)
            RETURN milestone AS node, 'Milestone' AS node_label, 'CONTAINS' AS relation, anchor.score AS anchor_score
            UNION
            WITH a, anchor
            OPTIONAL MATCH (a:Scene)-[rel:RELATES_TO|DERIVED_FROM]->(entity:EntityInstance)
            RETURN entity AS node, 'EntityInstance' AS node_label, type(rel) AS relation, anchor.score AS anchor_score
            UNION
            WITH a, anchor
            OPTIONAL MATCH (a:Scene)-[rel:FOLLOWED_BY|PRECEDED_BY]->(neighbor:Scene)
            RETURN neighbor AS node, 'Scene' AS node_label, type(rel) AS relation, anchor.score AS anchor_score
            UNION
            WITH a, anchor
            OPTIONAL MATCH (scene:Scene)-[:CONTAINS]->(a:Milestone)
            RETURN scene AS node, 'Scene' AS node_label, 'CONTAINS_PARENT' AS relation, anchor.score AS anchor_score
            UNION
            WITH a, anchor
            OPTIONAL MATCH (a:Milestone)-[rel:RELATES_TO|DERIVED_FROM]->(entity:EntityInstance)
            RETURN entity AS node, 'EntityInstance' AS node_label, type(rel) AS relation, anchor.score AS anchor_score
            UNION
            WITH a, anchor
            OPTIONAL MATCH (a:Milestone)-[rel:FOLLOWED_BY|PRECEDED_BY]->(neighbor:Milestone)
            RETURN neighbor AS node, 'Milestone' AS node_label, type(rel) AS relation, anchor.score AS anchor_score
        }}
        WITH DISTINCT node, node_label, relation, anchor_score
        WHERE node IS NOT NULL
          AND ($ontology_id IS NULL OR toInteger(node.ontology_id) = toInteger($ontology_id))
        OPTIONAL MATCH (node)-[:HAS_CHUNK]->(chunk:EntityChunk)
        WITH node, node_label, relation, anchor_score,
             head(collect(chunk)) AS chunk,
             CASE WHEN EXISTS {{ MATCH (node)-[:FOLLOWED_BY|PRECEDED_BY]-() }} THEN 1 ELSE 0 END AS has_order_edge
        RETURN node,
               node_label,
               relation,
               anchor_score,
               chunk,
               has_order_edge,
               coalesce(node.created_at, node.updated_at, '') AS temporal_value
        ORDER BY has_order_edge DESC, temporal_value {order_direction}, coalesce(node.name, node.alias, node.id, node.entity_instance_id) ASC
        LIMIT 60
        """
        result = await session.run(
            query,
            anchors=anchor_payload,
            ontology_id=ontology_id,
        )
        return await result.data()

    def _merge_anchor_and_expanded_chunks(
        self,
        *,
        anchors: list[dict[str, Any]],
        expanded: list[dict[str, Any]],
        ontology_id: int | None,
        temporal_mode: str,
    ) -> list[RetrievedChunk]:
        chunks_by_key: dict[str, RetrievedChunk] = {}

        def _put(chunk: RetrievedChunk) -> None:
            key = f"{chunk.node_label}::{chunk.node_id}"
            current = chunks_by_key.get(key)
            if current is None or float(chunk.score) > float(current.score):
                chunks_by_key[key] = chunk

        for anchor in anchors:
            score = float(anchor.get("score") or 0.0)
            breakdown = {
                "vector_best": float(anchor.get("vector_score") or 0.0),
                "fulltext_best": float(anchor.get("fulltext_score") or 0.0),
                "keyword_overlap": float(anchor.get("keyword_overlap") or 0.0),
            }
            _put(
                RetrievedChunk(
                    node_id=str(anchor["node_id"]),
                    node_label=anchor.get("node_label"),
                    node_name=anchor.get("node_name"),
                    node_alias=anchor.get("node_alias"),
                    instance_id=anchor.get("instance_id"),
                    chunk_id=anchor.get("chunk_id"),
                    chunk_type=anchor.get("chunk_type"),
                    chunk_index=anchor.get("chunk_index"),
                    text=str(anchor.get("context_text") or anchor.get("node_name") or anchor["node_id"]),
                    score=score,
                    confidence_pct=round(score * 100, 2),
                    source=f"hybrid_ontology_{ontology_id}" if ontology_id else "hybrid",
                    properties=anchor.get("properties") or {},
                    chunk_score=score,
                    node_score=score,
                    importance_index=score,
                    matched_chunk_count=int(anchor.get("matched_chunk_count") or 1),
                    score_breakdown=breakdown,
                    evidence_bundle={
                        "parent_type": anchor.get("node_label"),
                        "parent_id": anchor.get("node_id"),
                        "temporal_mode": temporal_mode,
                        "anchor": True,
                    },
                )
            )

        for idx, row in enumerate(expanded):
            node = row.get("node")
            if node is None:
                continue
            props = self._as_props(node)
            labels = self._as_labels(node)
            label = str(row.get("node_label") or self._primary_label(labels))
            node_id = self._node_id(props)
            if not node_id:
                continue
            chunk_props = self._as_props(row.get("chunk"))
            base_score = float(row.get("anchor_score") or 0.35)
            expansion_score = max(0.05, min(0.97, base_score - 0.04 - (idx * 0.001)))
            text = str(
                chunk_props.get("text_chunk")
                or props.get("description")
                or props.get("text")
                or props.get("autogenerated_text")
                or props.get("name")
                or props.get("alias")
                or node_id
            )
            _put(
                RetrievedChunk(
                    node_id=node_id,
                    node_label=label,
                    node_name=props.get("name"),
                    node_alias=props.get("alias"),
                    instance_id=props.get("instance_id"),
                    chunk_id=chunk_props.get("chunk_id") or f"{node_id}-expanded",
                    chunk_type=chunk_props.get("chunk_type") or "temporal_expansion",
                    chunk_index=chunk_props.get("chunk_index"),
                    text=text,
                    score=expansion_score,
                    confidence_pct=round(expansion_score * 100, 2),
                    source="hybrid_temporal_expansion",
                    properties={
                        **props,
                        "expanded_relation": row.get("relation"),
                        "temporal_mode": temporal_mode,
                        "has_order_edge": bool(row.get("has_order_edge")),
                    },
                    chunk_score=expansion_score,
                    node_score=expansion_score,
                    importance_index=expansion_score,
                    matched_chunk_count=1,
                    score_breakdown={
                        "anchor_score": base_score,
                        "temporal_expansion": expansion_score,
                    },
                    graph_boost=0.0,
                    evidence_bundle={
                        "parent_type": label,
                        "parent_id": node_id,
                        "temporal_mode": temporal_mode,
                        "expanded_relation": row.get("relation"),
                    },
                )
            )

        return sorted(
            chunks_by_key.values(),
            key=lambda item: float(item.importance_index or item.score),
            reverse=True,
        )
