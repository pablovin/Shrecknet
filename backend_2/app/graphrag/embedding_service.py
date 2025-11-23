"""Embedding service for creating and managing text embeddings."""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import os
import threading
import uuid
from typing import Any

import numpy as np
from neo4j import AsyncSession as AsyncNeo4jSession
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)


# Multilingual model with good performance/speed tradeoff
EMBED_MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBED_DIM = 384  # paraphrase-multilingual-MiniLM-L12-v2 dimension
EMBED_DEVICE = os.environ.get("SHRECKNET_EMBEDDING_DEVICE", "cpu")

# Thread-safe model loading
_model_lock = threading.Lock()
_cached_model: SentenceTransformer | None = None


def get_embedding_model() -> SentenceTransformer:
    """Get cached embedding model instance with thread-safe loading."""
    global _cached_model

    # Fast path: model already loaded
    if _cached_model is not None:
        return _cached_model

    # Slow path: need to load model (thread-safe)
    with _model_lock:
        # Double-check after acquiring lock
        if _cached_model is not None:
            return _cached_model

        # Load the model
        _cached_model = SentenceTransformer(EMBED_MODEL_ID, device=EMBED_DEVICE)
        return _cached_model


class EmbeddingService:
    """Service for creating embeddings and managing them in Neo4j."""

    def __init__(self, graph_session: AsyncNeo4jSession) -> None:
        self.graph_session = graph_session
        self.model_id = EMBED_MODEL_ID
        self.embed_dim = EMBED_DIM

    # ----------------------------
    # Chunking helpers
    # ----------------------------
    @staticmethod
    def _chunk_text(text: str, size: int = 900, overlap: int = 150) -> list[str]:
        text = (text or "").strip()
        if not text:
            return []
        chunks: list[str] = []
        i = 0
        n = len(text)
        while i < n:
            end = min(n, i + size)
            chunk = text[i:end]
            chunk = chunk.strip()
            if chunk:
                chunks.append(chunk)
            if end >= n:
                break
            i = end - overlap
            if i < 0:
                i = 0
        return chunks

    @staticmethod
    def _parse_properties(raw: Any) -> dict[str, Any]:
        if not raw:
            return {}
        if isinstance(raw, dict):
            return {str(k): v for k, v in raw.items()}
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return {str(k): v for k, v in parsed.items()}
            except json.JSONDecodeError:
                return {}
        return {}

    @staticmethod
    def _build_properties_chunk_text(prop_map: dict[str, Any]) -> str | None:
        if not prop_map:
            return None
        lines: list[str] = []
        for key in sorted(prop_map):
            value = prop_map[key]
            if value in (None, "", []):
                continue
            if isinstance(value, (dict, list)):
                value_text = json.dumps(value, sort_keys=True)
            else:
                value_text = str(value)
            lines.append(f"{key}: {value_text}")
        if not lines:
            return None
        formatted = "\n".join(lines)
        return f"Properties:\n{formatted}"

    @staticmethod
    def _build_relationships_chunk_text(
        relationships: list[dict[str, Any]] | None,
    ) -> str | None:
        if not relationships:
            return None
        lines: list[str] = []
        for rel in relationships:
            if not rel:
                continue
            rel_type = (
                rel.get("type")
                or rel.get("rel_type")
                or rel.get("relationship_type")
                or "RELATED_TO"
            )
            target = (
                rel.get("target_alias")
                or rel.get("target_name")
                or rel.get("target_id")
                or "Unknown"
            )
            label = rel.get("target_label") or rel.get("target_definition_id")
            extra_parts: list[str] = []
            rel_def = rel.get("relationship_definition_id")
            if rel_def:
                extra_parts.append(f"definition_id={rel_def}")
            data = rel.get("data")
            if isinstance(data, str):
                try:
                    parsed_data = json.loads(data)
                except json.JSONDecodeError:
                    parsed_data = data
                data = parsed_data
            if data:
                if isinstance(data, (dict, list)):
                    data_text = json.dumps(data, sort_keys=True)
                else:
                    data_text = str(data)
                extra_parts.append(f"data={data_text}")
            extra = f" [{'; '.join(extra_parts)}]" if extra_parts else ""
            label_part = f" ({label})" if label else ""
            lines.append(f"{rel_type} -> {target}{label_part}{extra}")
        if not lines:
            return None
        formatted = "\n".join(lines)
        return f"Relationships:\n{formatted}"

    def _build_entity_chunk_texts(
        self,
        text: str,
        autogenerated_text: str,
        properties_payload: Any,
        relationships: list[dict[str, Any]] | None,
    ) -> list[tuple[str, str]]:
        chunk_items: list[tuple[str, str]] = []
        text_chunks = self._chunk_text(text) or self._chunk_text(autogenerated_text)
        for chunk in text_chunks:
            chunk_items.append(("text", chunk))

        prop_map = self._parse_properties(properties_payload)
        properties_chunk = self._build_properties_chunk_text(prop_map)
        if properties_chunk:
            chunk_items.append(("properties", properties_chunk))

        relationships_chunk = self._build_relationships_chunk_text(relationships)
        if relationships_chunk:
            chunk_items.append(("relationships", relationships_chunk))

        return chunk_items

    @staticmethod
    def _build_timeline_chunk_text(
        title: str,
        description: str,
        source_instance_id: str | None,
        related_instance_ids: list[str] | None,
        before_event_id: str | None,
        after_event_id: str | None,
        *,
        source_instance_name: str | None = None,
        related_instance_names: list[str] | None = None,
        before_event_title: str | None = None,
        after_event_title: str | None = None,
    ) -> str | None:
        lines = [f"Timeline Event: {title}".strip()]
        if description:
            lines.append(description.strip())
        if source_instance_id:
            display = source_instance_name or source_instance_id
            if source_instance_name:
                display = f"{source_instance_name} ({source_instance_id})"
            lines.append(f"Source Entity: {display}")
        related_names = related_instance_names or []
        if related_instance_ids and not related_names:
            related_names = related_instance_ids
        if related_names:
            formatted = ", ".join(str(value) for value in related_names if value)
            if formatted:
                lines.append(f"Involved Entities: {formatted}")
        if before_event_id or before_event_title:
            label = before_event_title or before_event_id
            if before_event_title and before_event_id:
                label = f"{before_event_title} ({before_event_id})"
            lines.append(f"Occurs After Event: {label}")
        if after_event_id or after_event_title:
            label = after_event_title or after_event_id
            if after_event_title and after_event_id:
                label = f"{after_event_title} ({after_event_id})"
            lines.append(f"Precedes Event: {label}")
        lines = [line for line in lines if line]
        return "\n".join(lines) if lines else None

    async def _refresh_chunks(
        self, parent_id: str, chunk_plan: list[tuple[str, str]]
    ) -> None:
        await self.graph_session.run(
            """
            MATCH (parent {entity_instance_id: $eid})-[:HAS_CHUNK]->(c:EntityChunk)
            DETACH DELETE c
            """,
            eid=parent_id,
        )

        if not chunk_plan:
            return

        loop = asyncio.get_event_loop()
        chunk_texts = [text for _, text in chunk_plan]
        embeddings = await loop.run_in_executor(None, self.embed_texts, chunk_texts)

        rows = [
            {
                "entity_id": parent_id,
                "chunk_id": str(uuid.uuid4()),
                "chunk_index": idx,
                "chunk_type": chunk_type,
                "text": chunk_text,
                "embedding": embedding,
            }
            for idx, ((chunk_type, chunk_text), embedding) in enumerate(
                zip(chunk_plan, embeddings)
            )
        ]

        create_query = """
        UNWIND $rows AS row
        MATCH (parent {entity_instance_id: row.entity_id})
        WHERE parent:EntityInstance OR parent:TimelineEvent
        CREATE (c:EntityChunk {
            chunk_id: row.chunk_id,
            parent_entity_instance_id: row.entity_id,
            instance_id: parent.instance_id,
            ontology_id: parent.ontology_id,
            chunk_index: row.chunk_index,
            chunk_type: row.chunk_type,
            text_chunk: row.text,
            text_embedding: row.embedding,
            text_embedding_model: $model_id,
            text_embedding_dim: $embed_dim,
            is_embedded: true,
            last_embedded_date: datetime()
        })
        CREATE (parent)-[:HAS_CHUNK]->(c)
        SET parent.is_embedded = true,
            parent.last_embedded_date = datetime()
        """

        create_result = await self.graph_session.run(
            create_query,
            rows=rows,
            model_id=self.model_id,
            embed_dim=self.embed_dim,
        )
        await create_result.consume()

    async def _refresh_entity_chunks(
        self,
        entity_id: str,
        text: str,
        autogenerated_text: str,
        properties_payload: Any,
        relationships: list[dict[str, Any]] | None,
    ) -> None:
        chunk_plan = self._build_entity_chunk_texts(
            text, autogenerated_text, properties_payload, relationships
        )
        await self._refresh_chunks(entity_id, chunk_plan)

    async def _refresh_timeline_chunks(
        self,
        event_id: str,
        title: str,
        description: str,
        source_instance_id: str | None,
        related_instance_ids: list[str] | None,
        before_event_id: str | None,
        after_event_id: str | None,
        *,
        source_instance_name: str | None = None,
        related_instance_names: list[str] | None = None,
        before_event_title: str | None = None,
        after_event_title: str | None = None,
    ) -> None:
        summary = self._build_timeline_chunk_text(
            title,
            description,
            source_instance_id,
            related_instance_ids,
            before_event_id,
            after_event_id,
            source_instance_name=source_instance_name,
            related_instance_names=related_instance_names,
            before_event_title=before_event_title,
            after_event_title=after_event_title,
        )
        if not summary:
            return
        await self._refresh_chunks(event_id, [("timeline_event", summary)])

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a list of texts using the multilingual model.

        Args:
            texts: List of text strings to embed

        Returns:
            List of embedding vectors
        """
        global _cached_model
        
        model = get_embedding_model()
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                embeddings = model.encode(texts, normalize_embeddings=True)
                
                # Convert to numpy array with C-contiguous memory layout
                # This ensures a clean copy without buffer export locks
                # Using float32 to match the embedding model's native precision
                # (sentence-transformers outputs float32 by default)
                embeddings_array = np.asarray(embeddings, dtype=np.float32, order='C')
                
                # Convert to Python list row by row to avoid buffer reference issues
                # row.copy() creates a new array without buffer locks
                # .tolist() then converts to Python list
                # Both operations are necessary to break all buffer references
                result = [row.copy().tolist() for row in embeddings_array]
                
                return result
                
            except (RuntimeError, ValueError, BufferError) as exc:
                error_msg = str(exc)
                # String matching is necessary because these buffer errors don't have
                # specific exception types - they're generic numpy/PyTorch errors
                # with diagnostic messages
                is_retryable = (
                    "meta tensor" in error_msg.lower() or
                    "cannot be re-sized" in error_msg or
                    "export" in error_msg.lower() or
                    "buffer" in error_msg.lower()
                )
                
                if is_retryable:
                    logger.warning(
                        "Embedding error on attempt %d/%d: %s. Reloading model...",
                        attempt + 1,
                        max_retries,
                        exc,
                    )
                    
                    # Force garbage collection to release any lingering references
                    gc.collect()
                    
                    # Clear cached model and reload
                    with _model_lock:
                        _cached_model = None
                    
                    model = get_embedding_model()
                    
                    # On the last attempt, raise the exception
                    if attempt == max_retries - 1:
                        logger.error(
                            "Failed to embed texts after %d attempts: %s",
                            max_retries,
                            exc
                        )
                        raise
                else:
                    # Non-retryable error, raise immediately
                    raise

    def embed_text(self, text: str) -> list[float]:
        """
        Embed a single text.

        Args:
            text: Text string to embed

        Returns:
            Embedding vector
        """
        return self.embed_texts([text])[0]

    async def build_context_text(
        self,
        node_data: dict[str, Any],
        ontology_path: list[str] | None = None,
        relations: list[dict[str, str]] | None = None,
    ) -> str:
        """
        Build context text from node data for embedding.

        Args:
            node_data: Dict with 'name', 'labels', 'properties'
            ontology_path: List of ontology classes (breadcrumb)
            relations: List of dicts with 'type', 'target_name', 'target_label'

        Returns:
            Formatted context text
        """
        name = node_data.get("name", "Unknown")
        labels = node_data.get("labels", [])
        properties = node_data.get("properties", {})
        summary = node_data.get("summary", "")

        labels_csv = ", ".join(labels) if labels else "None"

        # Filter and format properties
        salient_props = []
        skip_keys = {
            "internal_id",
            "context_text",
            "text_embedding",
            "text_embedding_model",
            "text_embedding_dim",
            "graph_embedding",
            "graph_embedding_dim",
        }
        for k, v in properties.items():
            if k not in skip_keys and v:
                salient_props.append(f"{k}={v}")
        props_text = "; ".join(salient_props) if salient_props else "None"

        # Build ontology breadcrumb
        breadcrumb = " > ".join(ontology_path) if ontology_path else "None"

        # Build relations text (limit to top 6)
        rel_parts = []
        if relations:
            for rel in relations[:6]:
                rel_type = rel.get("type", "RELATED_TO")
                target_name = rel.get("target_name", "Unknown")
                target_label = rel.get("target_label", "")
                rel_parts.append(f"{rel_type} -> {target_name} ({target_label})")
        rel_text = "; ".join(rel_parts) if rel_parts else "None"

        context_text = f"""Name: {name}
Labels: {labels_csv}
Ontology: {breadcrumb}
Properties: {props_text}
Relations: {rel_text}
Summary: {summary}"""

        return context_text

    async def fetch_and_build_context(
        self, node_id: str, ontology_id: int | None = None
    ) -> tuple[str, dict[str, Any]]:
        """
        Fetch node data from Neo4j and build context text.

        Args:
            node_id: Neo4j node ID (entity_instance_id)
            ontology_id: Optional ontology ID filter

        Returns:
            Tuple of (context_text, node_data, relations)
        """
        # Query to fetch node with its relationships
        query = """
        MATCH (n:EntityInstance {entity_instance_id: $node_id})
        WHERE $ontology_id IS NULL OR n.ontology_id = $ontology_id
        OPTIONAL MATCH (n)-[r]->(m:EntityInstance)
        WITH n, collect({
            type: type(r),
            target_name: m.name,
            target_label: head(labels(m))
        }) AS rels
        RETURN n, rels
        LIMIT 1
        """

        result = await self.graph_session.run(
            query, node_id=node_id, ontology_id=ontology_id
        )
        record = await result.single()

        if not record:
            raise ValueError(f"Node {node_id} not found")

        node = record["n"]
        relations = record["rels"]

        node_data = {
            "name": node.get("name", "Unknown"),
            "labels": list(node.labels),
            "properties": dict(node),
            "summary": node.get("autogenerated_text", "") or node.get("text", ""),
        }

        # For now, use labels as ontology path (can be enhanced later)
        ontology_path = list(node.labels)

        context_text = await self.build_context_text(
            node_data, ontology_path, relations
        )

        return context_text, node_data, relations

    async def embed_node(
        self,
        node_id: str,
        ontology_id: int | None = None,
        *,
        regenerate_chunks: bool = True,
    ) -> dict[str, Any]:
        """
        Embed a single node and update it in Neo4j.

        Args:
            node_id: Neo4j node ID (entity_instance_id)
            ontology_id: Optional ontology ID filter
            regenerate_chunks: Whether to rebuild per-node chunks

        Returns:
            Dict with embedding info
        """
        context_text, node_data, relations = await self.fetch_and_build_context(
            node_id, ontology_id
        )

        # Embed in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        embedding = await loop.run_in_executor(None, self.embed_text, context_text)

        # Update node in Neo4j
        update_query = """
        MATCH (n:EntityInstance {entity_instance_id: $node_id})
        SET n.context_text = $context_text,
            n.text_embedding = $embedding,
            n.text_embedding_model = $model_id,
            n.text_embedding_dim = $embed_dim,
            n.is_embedded = true,
            n.last_embedded_date = datetime()
        RETURN n.entity_instance_id AS id
        """

        result = await self.graph_session.run(
            update_query,
            node_id=node_id,
            context_text=context_text,
            embedding=embedding,
            model_id=self.model_id,
            embed_dim=self.embed_dim,
        )
        await result.consume()

        if regenerate_chunks:
            node_properties = node_data.get("properties", {})
            raw_text = (node_properties.get("text") or "").strip()
            raw_auto = (node_properties.get("autogenerated_text") or "").strip()
            raw_properties = node_properties.get("properties")
            await self._refresh_entity_chunks(
                node_id, raw_text, raw_auto, raw_properties, relations
            )

        return {
            "node_id": node_id,
            "context_text": context_text,
            "embedding_model": self.model_id,
            "embedding_dim": self.embed_dim,
        }

    async def embed_ontology(
        self, ontology_id: int, batch_size: int = 50
    ) -> dict[str, Any]:
        """
        Embed nodes for a specific ontology that need embedding.

        Only processes nodes that are:
        - Not yet embedded (is_embedded is NULL or false)
        - Outdated (last_updated_date > last_embedded_date)

        Args:
            ontology_id: Ontology ID to embed
            batch_size: Number of nodes to process in each batch

        Returns:
            Dict with statistics
        """
        # Fetch node IDs that need embedding
        query = """
        MATCH (n)
        WHERE (n:EntityInstance OR n:TimelineEvent)
          AND n.ontology_id = $ontology_id
          AND (n.is_embedded IS NULL OR n.is_embedded = false 
               OR n.last_updated_date > n.last_embedded_date)
        RETURN n.entity_instance_id AS node_id
        """

        result = await self.graph_session.run(query, ontology_id=ontology_id)
        records = await result.data()
        node_ids = [r["node_id"] for r in records]

        if not node_ids:
            return {
                "ontology_id": ontology_id,
                "nodes_processed": 0,
                "nodes_failed": 0,
            }

        # Process in batches
        nodes_processed = 0
        nodes_failed = 0

        for i in range(0, len(node_ids), batch_size):
            batch = node_ids[i : i + batch_size]

            # Fetch raw texts for batch
            fetch_query = """
            UNWIND $ids AS eid
            MATCH (n {entity_instance_id: eid})
            WHERE n:EntityInstance OR n:TimelineEvent
            OPTIONAL MATCH (n)-[r]->(target:EntityInstance)
            WITH n, collect(
                CASE
                    WHEN r IS NULL THEN NULL
                    ELSE {
                        type: type(r),
                        relationship_definition_id: r.relationship_definition_id,
                        destiny_entity_definition_id: r.destiny_entity_definition_id,
                        target_alias: target.alias,
                        target_id: target.entity_instance_id,
                        target_definition_id: target.entity_definition_id,
                        target_label: head(labels(target)),
                        data: r.data
                    }
                END
            ) AS rels
            OPTIONAL MATCH (n)-[source_rel]->(source_entity:EntityInstance)
            WITH
                n,
                rels,
                CASE
                    WHEN source_rel IS NOT NULL AND type(source_rel) = 'SOURCE_ENTITY'
                    THEN source_entity
                    ELSE NULL
                END AS source_entity
            OPTIONAL MATCH (n)-[related_rel]->(related_entity:EntityInstance)
            WITH
                n,
                rels,
                source_entity,
                CASE
                    WHEN related_rel IS NOT NULL AND type(related_rel) = 'INVOLVES_ENTITY'
                    THEN related_entity
                    ELSE NULL
                END AS related_entity
            WITH
                n,
                rels,
                source_entity,
                [alias IN collect(DISTINCT related_entity.alias) WHERE alias IS NOT NULL] AS related_aliases
            OPTIONAL MATCH (fallback_source:EntityInstance)
            WHERE fallback_source.entity_instance_id = n.source_instance_id
            WITH
                n,
                rels,
                related_aliases,
                coalesce(
                    source_entity.alias,
                    source_entity.name,
                    fallback_source.alias,
                    fallback_source.name
                ) AS source_entity_name
            OPTIONAL MATCH (fallback_related:EntityInstance)
            WHERE n.related_instance_ids IS NOT NULL
              AND fallback_related.entity_instance_id IN n.related_instance_ids
            WITH
                n,
                rels,
                source_entity_name,
                related_aliases,
                [alias IN collect(DISTINCT fallback_related.alias) WHERE alias IS NOT NULL] AS fallback_related_aliases
            WITH
                n,
                rels,
                source_entity_name,
                CASE
                    WHEN size(related_aliases) > 0 THEN related_aliases
                    ELSE fallback_related_aliases
                END AS related_instance_names
            OPTIONAL MATCH (before_event:TimelineEvent {timeline_event_id: n.before_event_id})
            OPTIONAL MATCH (after_event:TimelineEvent {timeline_event_id: n.after_event_id})
            RETURN n.entity_instance_id AS entity_id,
                   labels(n) AS labels,
                   coalesce(n.text, "") AS text,
                   coalesce(n.autogenerated_text, "") AS autogenerated_text,
                   n.properties AS properties,
                   rels AS relationships,
                   n.title AS timeline_title,
                   n.description AS timeline_description,
                   n.source_instance_id AS source_instance_id,
                   n.related_instance_ids AS related_instance_ids,
                   n.before_event_id AS before_event_id,
                   n.after_event_id AS after_event_id,
                   source_entity_name AS source_instance_name,
                   related_instance_names AS related_instance_names,
                   before_event.title AS before_event_title,
                   after_event.title AS after_event_title
            """
            fetch_res = await self.graph_session.run(fetch_query, ids=batch)
            fetch_rows = await fetch_res.data()

            for row in fetch_rows:
                eid = row["entity_id"]
                labels = row.get("labels") or []
                is_timeline = "TimelineEvent" in labels
                raw_text = (row.get("text") or "").strip()
                raw_auto = (row.get("autogenerated_text") or "").strip()
                properties_payload = row.get("properties")
                relationships = [rel for rel in (row.get("relationships") or []) if rel]
                try:
                    if is_timeline:
                        await self._refresh_timeline_chunks(
                            eid,
                            row.get("timeline_title") or row.get("text") or "",
                            row.get("timeline_description") or "",
                            row.get("source_instance_id"),
                            row.get("related_instance_ids"),
                            row.get("before_event_id"),
                            row.get("after_event_id"),
                            source_instance_name=row.get("source_instance_name"),
                            related_instance_names=row.get("related_instance_names"),
                            before_event_title=row.get("before_event_title"),
                            after_event_title=row.get("after_event_title"),
                        )
                        nodes_processed += 1
                    else:
                        await self._refresh_entity_chunks(
                            eid, raw_text, raw_auto, properties_payload, relationships
                        )
                        await self.embed_node(eid, ontology_id, regenerate_chunks=False)
                        nodes_processed += 1
                except Exception:
                    nodes_failed += 1

        return {
            "ontology_id": ontology_id,
            "nodes_processed": nodes_processed,
            "nodes_failed": nodes_failed,
        }

    async def backfill_chunks(
        self, ontology_id: int, min_chars: int = 800, batch_size: int = 50
    ) -> dict[str, Any]:
        """Create chunk nodes only for entities with large texts.

        - If e.text or e.autogenerated_text length >= min_chars, (re)chunk and embed.
        - Small nodes are left as-is to save time.
        """
        # Find candidate entities
        query = """
        MATCH (e:EntityInstance)
        WHERE e.ontology_id = $ontology_id
        WITH e, size(coalesce(e.text, "")) AS tlen, size(coalesce(e.autogenerated_text, "")) AS alen
        WITH e, CASE WHEN tlen > alen THEN tlen ELSE alen END AS mlen
        WHERE mlen >= $min_chars
        RETURN e.entity_instance_id AS entity_id
        """
        res = await self.graph_session.run(
            query, ontology_id=ontology_id, min_chars=min_chars
        )
        rows = await res.data()
        ids = [r["entity_id"] for r in rows]
        processed = 0
        failed = 0

        for i in range(0, len(ids), batch_size):
            batch = ids[i : i + batch_size]
            try:
                # Reuse the embed_ontology chunk path by marking only these ids
                fetch_query = """
                UNWIND $ids AS eid
                MATCH (e:EntityInstance {entity_instance_id: eid})
                OPTIONAL MATCH (e)-[r]->(target:EntityInstance)
                WITH e, collect(
                    CASE
                        WHEN r IS NULL THEN NULL
                        ELSE {
                            type: type(r),
                            relationship_definition_id: r.relationship_definition_id,
                            destiny_entity_definition_id: r.destiny_entity_definition_id,
                            target_alias: target.alias,
                            target_id: target.entity_instance_id,
                            target_definition_id: target.entity_definition_id,
                            target_label: head(labels(target)),
                            data: r.data
                        }
                    END
                ) AS rels
                RETURN e.entity_instance_id AS entity_id,
                       coalesce(e.text, "") AS text,
                       coalesce(e.autogenerated_text, "") AS autogenerated_text,
                       e.properties AS properties,
                       rels AS relationships
                """
                fetch_res = await self.graph_session.run(fetch_query, ids=batch)
                fetch_rows = await fetch_res.data()
                for row in fetch_rows:
                    eid = row["entity_id"]
                    raw_text = (row.get("text") or "").strip()
                    raw_auto = (row.get("autogenerated_text") or "").strip()
                    properties_payload = row.get("properties")
                    relationships = [
                        rel for rel in (row.get("relationships") or []) if rel
                    ]
                    await self._refresh_entity_chunks(
                        eid, raw_text, raw_auto, properties_payload, relationships
                    )
                processed += len(fetch_rows)
            except Exception:
                failed += len(batch)
                continue
        return {
            "ontology_id": ontology_id,
            "nodes_processed": processed,
            "nodes_failed": failed,
        }

    async def reset_ontology_embeddings(self, ontology_id: int) -> dict[str, int]:
        """Remove embeddings, chunk nodes, and orphan entities for an ontology."""
        delete_chunks_query = """
        MATCH (parent {ontology_id: $ontology_id})-[:HAS_CHUNK]->(chunk:EntityChunk)
        WHERE parent:EntityInstance OR parent:TimelineEvent
        WITH collect(DISTINCT chunk) AS chunks
        CALL (chunks) {
            UNWIND chunks AS chunk
            DETACH DELETE chunk
        }
        RETURN size(chunks) AS deleted_chunks
        """
        chunk_result = await self.graph_session.run(
            delete_chunks_query, ontology_id=ontology_id
        )
        chunk_record = await chunk_result.single()
        deleted_chunks = chunk_record["deleted_chunks"] if chunk_record else 0

        delete_orphans_query = """
        MATCH (e:EntityInstance {ontology_id: $ontology_id})
        WHERE NOT ( (:OntologyInstance {ontology_id: $ontology_id})-[:HAS_ENTITY]->(e) )
        WITH collect(DISTINCT e) AS orphans
        CALL (orphans) {
            UNWIND orphans AS orphan
            DETACH DELETE orphan
        }
        RETURN size(orphans) AS deleted_orphans
        """
        orphan_result = await self.graph_session.run(
            delete_orphans_query, ontology_id=ontology_id
        )
        orphan_record = await orphan_result.single()
        deleted_orphans = orphan_record["deleted_orphans"] if orphan_record else 0

        reset_nodes_query = """
        MATCH (node {ontology_id: $ontology_id})
        WHERE node:EntityInstance OR node:TimelineEvent
        SET node.is_embedded = false,
            node.last_embedded_date = null
        REMOVE node.text_embedding,
               node.text_embedding_model,
               node.text_embedding_dim,
               node.context_text
        RETURN count(node) AS nodes_reset
        """
        node_result = await self.graph_session.run(
            reset_nodes_query, ontology_id=ontology_id
        )
        node_record = await node_result.single()
        nodes_reset = node_record["nodes_reset"] if node_record else 0

        return {
            "ontology_id": ontology_id,
            "nodes_reset": nodes_reset,
            "orphans_deleted": deleted_orphans,
            "chunks_deleted": deleted_chunks,
        }

    async def remove_instance_embeddings(self, instance_id: str) -> dict[str, int]:
        """
        Remove all embeddings and chunks for a specific ontology instance.
        
        This ensures that when an instance is deleted, all associated embeddings
        are properly cleaned up from the embedding space.
        
        Args:
            instance_id: The ontology instance ID to clean up
            
        Returns:
            Dictionary with counts of deleted chunks and reset nodes
        """
        # Delete all chunks associated with this instance
        delete_chunks_query = """
        MATCH (chunk:EntityChunk {instance_id: $instance_id})
        DETACH DELETE chunk
        RETURN count(*) AS deleted_chunks
        """
        chunk_result = await self.graph_session.run(
            delete_chunks_query, instance_id=instance_id
        )
        chunk_record = await chunk_result.single()
        deleted_chunks = chunk_record["deleted_chunks"] if chunk_record else 0

        # Reset embedding flags on any remaining nodes for this instance
        # (in case they exist outside the normal deletion flow)
        reset_nodes_query = """
        MATCH (node {instance_id: $instance_id})
        WHERE node:EntityInstance OR node:TimelineEvent
        SET node.is_embedded = false,
            node.last_embedded_date = null
        REMOVE node.text_embedding,
               node.text_embedding_model,
               node.text_embedding_dim,
               node.context_text
        RETURN count(node) AS nodes_reset
        """
        node_result = await self.graph_session.run(
            reset_nodes_query, instance_id=instance_id
        )
        node_record = await node_result.single()
        nodes_reset = node_record["nodes_reset"] if node_record else 0

        return {
            "instance_id": instance_id,
            "chunks_deleted": deleted_chunks,
            "nodes_reset": nodes_reset,
        }

    async def ensure_vector_index(
        self, index_name: str = "entity_text_vec_idx"
    ) -> bool:
        """
        Create Neo4j vector index if it doesn't exist.

        Args:
            index_name: Name for the vector index

        Returns:
            True if index was created or already exists
        """
        # Check if index exists
        check_query = "SHOW INDEXES YIELD name WHERE name = $index_name RETURN name"

        result = await self.graph_session.run(check_query, index_name=index_name)
        record = await result.single()

        if record:
            return True  # Index already exists

        # Create vector index (EntityInstance-level embeddings)
        create_query = f"""
        CREATE VECTOR INDEX {index_name}
        FOR (n:EntityInstance) ON (n.text_embedding)
        OPTIONS {{
            indexConfig: {{
                `vector.dimensions`: {self.embed_dim},
                `vector.similarity_function`: 'cosine'
            }}
        }}
        """

        try:
            result = await self.graph_session.run(create_query)
            await result.consume()
            return True
        except Exception as e:
            # Index might already exist or other error
            print(f"Error creating index: {e}")
            return False

    async def ensure_chunk_vector_index(
        self, index_name: str = "entity_chunk_vec_idx"
    ) -> bool:
        """Create vector index for EntityChunk if it doesn't exist."""
        check_query = "SHOW INDEXES YIELD name WHERE name = $index_name RETURN name"
        result = await self.graph_session.run(check_query, index_name=index_name)
        record = await result.single()
        if record:
            return True
        create_query = f"""
        CREATE VECTOR INDEX {index_name}
        FOR (c:EntityChunk) ON (c.text_embedding)
        OPTIONS {{
            indexConfig: {{
                `vector.dimensions`: {self.embed_dim},
                `vector.similarity_function`: 'cosine'
            }}
        }}
        """
        try:
            res = await self.graph_session.run(create_query)
            await res.consume()
            return True
        except Exception as e:
            print(f"Error creating chunk index: {e}")
            return False
