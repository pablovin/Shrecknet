"""Neo4j graph retriever for Elder pipeline."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Protocol

from neo4j import AsyncSession as AsyncNeo4jSession

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
        query = """
        MATCH (inst:OntologyInstance)-[:HAS_ENTITY]->(entity:EntityInstance)
          WHERE toInteger(inst.ontology_id) = toInteger($ontology_id)
              OR toInteger(entity.ontology_id) = toInteger($ontology_id)
        RETURN entity.entity_instance_id AS node_id,
               coalesce(entity.alias, entity.name, entity.entity_instance_id) AS alias,
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
