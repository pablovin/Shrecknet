"""Embedding service for creating and managing text embeddings."""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import threading
import uuid
from typing import Any

import numpy as np
from neo4j import AsyncSession as AsyncNeo4jSession
from sentence_transformers import SentenceTransformer

from app.core.config_store import get_settings

logger = logging.getLogger(__name__)


# Thread-safe model loading
_model_lock = threading.Lock()
_cached_model: SentenceTransformer | None = None
_cached_model_key: tuple[str, str] | None = None


def _current_model_key() -> tuple[str, str]:
    settings = get_settings()
    return (settings.embedding_model_id, settings.embedding_device)


def get_embedding_model_id() -> str:
    """Return the currently configured embedding model identifier."""
    return get_settings().embedding_model_id


def get_embedding_dimension() -> int:
    """Return the currently configured embedding vector dimension."""
    return get_settings().embedding_dimension


def get_embedding_model() -> SentenceTransformer:
    """Get cached embedding model instance with thread-safe loading."""
    global _cached_model, _cached_model_key
    model_key = _current_model_key()

    # Fast path: model already loaded
    if _cached_model is not None and _cached_model_key == model_key:
        return _cached_model

    # Slow path: need to load model (thread-safe)
    with _model_lock:
        # Double-check after acquiring lock
        if _cached_model is not None and _cached_model_key == model_key:
            return _cached_model

        model_id, device = model_key

        # Load the model
        _cached_model = SentenceTransformer(model_id, device=device)
        _cached_model_key = model_key
        return _cached_model


class EmbeddingService:
    """Service for creating embeddings and managing them in Neo4j."""

    def __init__(self, graph_session: AsyncNeo4jSession) -> None:
        self.graph_session = graph_session
        settings = get_settings()
        self.model_id = settings.embedding_model_id
        self.embed_dim = settings.embedding_dimension
        self.chunk_size = settings.embedding_chunk_size
        self.chunk_overlap = settings.embedding_chunk_overlap

    # ----------------------------
    # Chunking helpers
    # ----------------------------
    def _chunk_text(self, text: str, size: int | None = None, overlap: int | None = None) -> list[str]:
        text = (text or "").strip()
        if not text:
            return []
        chunk_size = size if size is not None else self.chunk_size
        chunk_overlap = overlap if overlap is not None else self.chunk_overlap
        chunks: list[str] = []
        i = 0
        n = len(text)
        while i < n:
            end = min(n, i + chunk_size)
            chunk = text[i:end]
            chunk = chunk.strip()
            if chunk:
                chunks.append(chunk)
            if end >= n:
                break
            i = end - chunk_overlap
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
        scene_participation: list[dict[str, Any]] | None = None,
        milestone_participation: list[dict[str, Any]] | None = None,
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

        scene_chunk = self._build_scene_participation_chunk_text(scene_participation)
        if scene_chunk:
            chunk_items.append(("scene_participation", scene_chunk))

        milestone_chunk = self._build_milestone_participation_chunk_text(
            milestone_participation
        )
        if milestone_chunk:
            chunk_items.append(("milestone_participation", milestone_chunk))

        return chunk_items

    @staticmethod
    def _build_scene_participation_chunk_text(
        scene_participation: list[dict[str, Any]] | None,
    ) -> str | None:
        if not scene_participation:
            return None
        lines: list[str] = []
        for item in scene_participation:
            if not isinstance(item, dict):
                continue
            scene_name = item.get("scene_name") or item.get("scene_id")
            if not scene_name:
                continue
            label = item.get("relation_label")
            description = item.get("description")
            bits = [f"Scene {scene_name}"]
            if label:
                bits.append(f"label={str(label).strip().lower()}")
            if description:
                bits.append(f"description={str(description).strip()}")
            lines.append(" | ".join(bits))
        if not lines:
            return None
        return "Scene Participation:\n" + "\n".join(lines)

    @staticmethod
    def _build_milestone_participation_chunk_text(
        milestone_participation: list[dict[str, Any]] | None,
    ) -> str | None:
        if not milestone_participation:
            return None
        lines: list[str] = []
        for item in milestone_participation:
            if not isinstance(item, dict):
                continue
            milestone_name = item.get("milestone_name") or item.get("milestone_id")
            if not milestone_name:
                continue
            scene_name = item.get("scene_name")
            label = item.get("relation_label")
            description = item.get("description")
            bits = [f"Milestone {milestone_name}"]
            if scene_name:
                bits.append(f"scene={scene_name}")
            if label:
                bits.append(f"label={str(label).strip().lower()}")
            if description:
                bits.append(f"description={str(description).strip()}")
            lines.append(" | ".join(bits))
        if not lines:
            return None
        return "Milestone Participation:\n" + "\n".join(lines)

    @staticmethod
    def _build_scene_main_chunk_text(
        *,
        scene_name: str,
        scene_description: str,
        ordered_milestones: list[dict[str, Any]],
        entity_links: list[dict[str, Any]],
    ) -> str | None:
        lines = [f"Scene: {scene_name}".strip()]
        if scene_description:
            lines.append(scene_description.strip())

        if ordered_milestones:
            lines.append("Ordered Milestones:")
            for idx, milestone in enumerate(ordered_milestones, start=1):
                name = milestone.get("name") or milestone.get("id") or f"m{idx}"
                temporal = milestone.get("temporal_type")
                boundary = milestone.get("boundary_type")
                description = milestone.get("description")
                bits = [f"{idx}. {name}"]
                if temporal:
                    bits.append(f"temporal={temporal}")
                if boundary:
                    bits.append(f"boundary={boundary}")
                if description:
                    bits.append(f"description={str(description).strip()}")
                lines.append(" | ".join(bits))

        if entity_links:
            lines.append("Entity Links:")
            for link in entity_links:
                entity_name = (
                    link.get("entity_alias")
                    or link.get("entity_name")
                    or link.get("entity_instance_id")
                )
                if not entity_name:
                    continue
                label = link.get("label")
                description = link.get("description")
                bits = [f"- {entity_name}"]
                if label:
                    bits.append(f"label={str(label).strip().lower()}")
                if description:
                    bits.append(f"description={str(description).strip()}")
                lines.append(" | ".join(bits))

        lines = [line for line in lines if line]
        return "\n".join(lines) if lines else None

    @staticmethod
    def _build_milestone_main_chunk_text(
        *,
        milestone_name: str,
        milestone_description: str,
        temporal_type: str | None,
        boundary_type: str | None,
        relates_to: list[dict[str, Any]],
    ) -> str | None:
        lines = [f"Milestone: {milestone_name}".strip()]
        if milestone_description:
            lines.append(milestone_description.strip())
        if temporal_type:
            lines.append(f"temporal_type: {temporal_type}")
        if boundary_type:
            lines.append(f"boundary_type: {boundary_type}")
        if relates_to:
            lines.append("Related Entities:")
            for rel in relates_to:
                entity_name = (
                    rel.get("entity_alias")
                    or rel.get("entity_name")
                    or rel.get("entity_instance_id")
                )
                if not entity_name:
                    continue
                label = rel.get("label")
                description = rel.get("description")
                bits = [f"- {entity_name}"]
                if label:
                    bits.append(f"label={str(label).strip().lower()}")
                if description:
                    bits.append(f"description={str(description).strip()}")
                lines.append(" | ".join(bits))

        lines = [line for line in lines if line]
        return "\n".join(lines) if lines else None


    async def _refresh_chunks(
        self, parent_id: str, chunk_plan: list[tuple[str, str]]
    ) -> None:
        await self.graph_session.run(
            """
            MATCH (parent)-[:HAS_CHUNK]->(c:EntityChunk)
            WHERE (parent:EntityInstance AND parent.entity_instance_id = $eid)
               OR (parent:Event AND parent.event_id = $eid)
               OR ((parent:Scene OR parent:Milestone) AND parent.id = $eid)
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
                "parent_id": parent_id,
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
        MATCH (parent)
        WHERE (parent:EntityInstance AND parent.entity_instance_id = row.parent_id)
           OR (parent:Event AND parent.event_id = row.parent_id)
           OR ((parent:Scene OR parent:Milestone) AND parent.id = row.parent_id)
        CREATE (c:EntityChunk {
            chunk_id: row.chunk_id,
            parent_entity_instance_id: row.parent_id,
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
        scene_participation: list[dict[str, Any]] | None = None,
        milestone_participation: list[dict[str, Any]] | None = None,
    ) -> None:
        chunk_plan = self._build_entity_chunk_texts(
            text,
            autogenerated_text,
            properties_payload,
            relationships,
            scene_participation,
            milestone_participation,
        )
        await self._refresh_chunks(entity_id, chunk_plan)

    async def _refresh_scene_chunks(
        self,
        *,
        scene_id: str,
        scene_name: str,
        scene_description: str,
        ordered_milestones: list[dict[str, Any]],
        entity_links: list[dict[str, Any]],
    ) -> None:
        summary = self._build_scene_main_chunk_text(
            scene_name=scene_name,
            scene_description=scene_description,
            ordered_milestones=ordered_milestones,
            entity_links=entity_links,
        )
        if not summary:
            return
        await self._refresh_chunks(scene_id, [("scene_main", summary)])

    async def _refresh_milestone_chunks(
        self,
        *,
        milestone_id: str,
        milestone_name: str,
        milestone_description: str,
        temporal_type: str | None,
        boundary_type: str | None,
        relates_to: list[dict[str, Any]],
    ) -> None:
        summary = self._build_milestone_main_chunk_text(
            milestone_name=milestone_name,
            milestone_description=milestone_description,
            temporal_type=temporal_type,
            boundary_type=boundary_type,
            relates_to=relates_to,
        )
        if not summary:
            return
        await self._refresh_chunks(milestone_id, [("milestone_main", summary)])

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a list of texts using the multilingual model.

        Args:
            texts: List of text strings to embed

        Returns:
            List of embedding vectors
        """
        global _cached_model, _cached_model_key

        model = get_embedding_model()
        max_retries = 3

        for attempt in range(max_retries):
            try:
                embeddings = model.encode(texts, normalize_embeddings=True)

                # Convert to numpy array with C-contiguous memory layout
                # This ensures a clean copy without buffer export locks
                # Using float32 to match the embedding model's native precision
                # (sentence-transformers outputs float32 by default)
                embeddings_array = np.asarray(embeddings, dtype=np.float32, order="C")

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
                    "meta tensor" in error_msg.lower()
                    or "cannot be re-sized" in error_msg
                    or "export" in error_msg.lower()
                    or "buffer" in error_msg.lower()
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
                        _cached_model_key = None

                    model = get_embedding_model()

                    # On the last attempt, raise the exception
                    if attempt == max_retries - 1:
                        logger.error(
                            "Failed to embed texts after %d attempts: %s",
                            max_retries,
                            exc,
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
        WHERE $ontology_id IS NULL OR toInteger(n.ontology_id) = toInteger($ontology_id)
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
        query = """
        MATCH (n)
                WHERE any(label IN labels(n) WHERE label IN ['EntityInstance', 'Scene', 'Milestone'])
          AND toInteger(n['ontology_id']) = toInteger($ontology_id)
          AND (
              n['is_embedded'] IS NULL OR n['is_embedded'] = false
              OR n['last_updated_date'] > n['last_embedded_date']
          )
        WITH n, properties(n) AS props
        RETURN CASE
                   WHEN 'EntityInstance' IN labels(n) THEN toString(props[$entity_id_key])
                   ELSE toString(props[$node_id_key])
               END AS node_id,
               CASE
                   WHEN 'EntityInstance' IN labels(n) THEN 'entity'
                   WHEN 'Scene' IN labels(n) THEN 'scene'
                   WHEN 'Milestone' IN labels(n) THEN 'milestone'
                   ELSE 'other'
               END AS node_type
        """

        result = await self.graph_session.run(
            query,
            ontology_id=ontology_id,
            entity_id_key="entity_instance_id",
            node_id_key="id",
        )
        records = await result.data()
        entity_ids = [r["node_id"] for r in records if r.get("node_type") == "entity" and r.get("node_id")]
        scene_ids = [r["node_id"] for r in records if r.get("node_type") == "scene" and r.get("node_id")]
        milestone_ids = [r["node_id"] for r in records if r.get("node_type") == "milestone" and r.get("node_id")]

        if not (entity_ids or scene_ids or milestone_ids):
            return {
                "ontology_id": ontology_id,
                "nodes_processed": 0,
                "nodes_failed": 0,
                "processed_by_type": {
                    "entities": 0,
                    "scenes": 0,
                    "milestones": 0,
                },
            }

        nodes_processed = 0
        nodes_failed = 0
        processed_by_type = {
            "entities": 0,
            "scenes": 0,
            "milestones": 0,
        }

        for i in range(0, len(entity_ids), batch_size):
            batch = entity_ids[i : i + batch_size]
            fetch_query = """
            UNWIND $ids AS eid
            MATCH (n:EntityInstance {entity_instance_id: eid})
            OPTIONAL MATCH (n)-[r:RELATES_TO]->(target:EntityInstance)
            WITH n, collect(
                CASE
                    WHEN r IS NULL THEN NULL
                    ELSE {
                        type: type(r),
                        relationship_definition_id: r['relationship_definition_id'],
                        destiny_entity_definition_id: r['destiny_entity_definition_id'],
                        target_alias: target['alias'],
                        target_id: target['entity_instance_id'],
                        target_definition_id: target['entity_definition_id'],
                        target_label: head(labels(target)),
                        data: r['data']
                    }
                END
            ) AS rels
            OPTIONAL MATCH (n)<-[derived_rel]-(scene)
            WHERE type(derived_rel) = 'DERIVED_FROM' AND 'Scene' IN labels(scene)
            WITH n, rels, collect(
                CASE
                    WHEN scene IS NULL THEN NULL
                    ELSE {
                        scene_id: toString(properties(scene)[$node_id_key]),
                        scene_name: scene.name,
                        relation_label: '',
                        description: scene.description
                    }
                END
            ) AS scene_participation
            OPTIONAL MATCH (n)<-[mrel]-(milestone)
            WHERE type(mrel) = 'RELATES_TO' AND 'Milestone' IN labels(milestone)
            OPTIONAL MATCH (milestone)<-[contains_rel]-(ms_scene)
            WHERE type(contains_rel) = 'CONTAINS' AND 'Scene' IN labels(ms_scene)
            RETURN n.entity_instance_id AS entity_id,
                   coalesce(n.text, '') AS text,
                   coalesce(n.autogenerated_text, '') AS autogenerated_text,
                   n.properties AS properties,
                   rels AS relationships,
                   [item IN scene_participation WHERE item IS NOT NULL] AS scene_participation,
                   collect(DISTINCT CASE WHEN milestone IS NULL THEN NULL ELSE {
                       milestone_id: toString(properties(milestone)[$node_id_key]),
                       milestone_name: milestone.name,
                       scene_name: ms_scene.name,
                       relation_label: toString(properties(mrel)[$relation_label_key]),
                       description: milestone.description
                   } END) AS milestone_participation
            """
            fetch_res = await self.graph_session.run(
                fetch_query,
                ids=batch,
                node_id_key="id",
                relation_label_key="label",
            )
            for row in await fetch_res.data():
                try:
                    await self._refresh_entity_chunks(
                        row["entity_id"],
                        (row.get("text") or "").strip(),
                        (row.get("autogenerated_text") or "").strip(),
                        row.get("properties"),
                        [rel for rel in (row.get("relationships") or []) if rel],
                        row.get("scene_participation") or [],
                        [item for item in (row.get("milestone_participation") or []) if item],
                    )
                    await self.embed_node(row["entity_id"], ontology_id, regenerate_chunks=False)
                    nodes_processed += 1
                    processed_by_type["entities"] += 1
                except Exception:
                    nodes_failed += 1

        for i in range(0, len(scene_ids), batch_size):
            batch = scene_ids[i : i + batch_size]
            fetch_query = """
            UNWIND $ids AS scene_id
            MATCH (scene:Scene {id: scene_id})
            OPTIONAL MATCH (scene)-[:CONTAINS]->(milestone:Milestone)
            OPTIONAL MATCH (milestone)-[rel:RELATES_TO]->(entity:EntityInstance)
            WITH scene,
                 collect(DISTINCT {
                     id: milestone.id,
                     name: milestone.name,
                     description: milestone.description,
                     temporal_type: milestone.temporal_type,
                     boundary_type: milestone.boundary_type,
                     created_at: milestone.created_at
                 }) AS milestones,
                 collect(DISTINCT {
                     entity_instance_id: entity.entity_instance_id,
                     entity_alias: entity.alias,
                     entity_name: entity.name,
                     label: rel.label,
                     description: milestone.description
                 }) AS entity_links
            RETURN scene.id AS scene_id,
                   scene.name AS scene_name,
                   scene.description AS scene_description,
                   [m IN milestones WHERE m.id IS NOT NULL] AS milestones,
                   [l IN entity_links WHERE l.entity_instance_id IS NOT NULL] AS entity_links
            """
            fetch_res = await self.graph_session.run(fetch_query, ids=batch)
            for row in await fetch_res.data():
                try:
                    milestones = sorted(
                        row.get("milestones") or [],
                        key=lambda item: str(item.get("created_at") or ""),
                    )
                    await self._refresh_scene_chunks(
                        scene_id=row["scene_id"],
                        scene_name=row.get("scene_name") or row["scene_id"],
                        scene_description=row.get("scene_description") or "",
                        ordered_milestones=milestones,
                        entity_links=row.get("entity_links") or [],
                    )
                    nodes_processed += 1
                    processed_by_type["scenes"] += 1
                except Exception:
                    nodes_failed += 1

        for i in range(0, len(milestone_ids), batch_size):
            batch = milestone_ids[i : i + batch_size]
            fetch_query = """
            UNWIND $ids AS milestone_id
            MATCH (milestone:Milestone {id: milestone_id})
            OPTIONAL MATCH (milestone)-[rel:RELATES_TO]->(entity:EntityInstance)
            RETURN milestone.id AS milestone_id,
                   milestone.name AS milestone_name,
                   milestone.description AS milestone_description,
                   milestone.temporal_type AS temporal_type,
                   milestone.boundary_type AS boundary_type,
                   collect(DISTINCT {
                     entity_instance_id: entity.entity_instance_id,
                     entity_alias: entity.alias,
                     entity_name: entity.name,
                     label: rel.label,
                     description: milestone.description
                   }) AS relates_to
            """
            fetch_res = await self.graph_session.run(fetch_query, ids=batch)
            for row in await fetch_res.data():
                try:
                    await self._refresh_milestone_chunks(
                        milestone_id=row["milestone_id"],
                        milestone_name=row.get("milestone_name") or row["milestone_id"],
                        milestone_description=row.get("milestone_description") or "",
                        temporal_type=row.get("temporal_type"),
                        boundary_type=row.get("boundary_type"),
                        relates_to=[item for item in (row.get("relates_to") or []) if item and item.get("entity_instance_id")],
                    )
                    nodes_processed += 1
                    processed_by_type["milestones"] += 1
                except Exception:
                    nodes_failed += 1

        return {
            "ontology_id": ontology_id,
            "nodes_processed": nodes_processed,
            "nodes_failed": nodes_failed,
            "processed_by_type": processed_by_type,
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
        WHERE toInteger(e.ontology_id) = toInteger($ontology_id)
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
        MATCH (parent)-[:HAS_CHUNK]->(chunk:EntityChunk)
                WHERE (parent:EntityInstance OR parent:Event OR parent:Scene OR parent:Milestone)
          AND toInteger(parent.ontology_id) = toInteger($ontology_id)
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
        MATCH (e:EntityInstance)
        WHERE toInteger(e.ontology_id) = toInteger($ontology_id)
          AND NOT ( (:OntologyInstance)-[:HAS_ENTITY]->(e) )
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
        MATCH (node)
                WHERE (node:EntityInstance OR node:Event OR node:Scene OR node:Milestone)
          AND toInteger(node.ontology_id) = toInteger($ontology_id)
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
        WHERE node:EntityInstance OR node:Event OR node:Scene OR node:Milestone
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


# Backward-compatible aliases for older imports.
EMBED_MODEL_ID = get_embedding_model_id()
EMBED_DIM = get_embedding_dimension()
