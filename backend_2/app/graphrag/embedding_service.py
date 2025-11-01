"""Embedding service for creating and managing text embeddings."""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any

from neo4j import AsyncSession as AsyncNeo4jSession
from sentence_transformers import SentenceTransformer


# Multilingual model with good performance/speed tradeoff
EMBED_MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBED_DIM = 384  # paraphrase-multilingual-MiniLM-L12-v2 dimension


@lru_cache(maxsize=1)
def get_embedding_model() -> SentenceTransformer:
    """Get cached embedding model instance."""
    return SentenceTransformer(EMBED_MODEL_ID)


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

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a list of texts using the multilingual model.

        Args:
            texts: List of text strings to embed

        Returns:
            List of embedding vectors
        """
        model = get_embedding_model()
        embeddings = model.encode(texts, normalize_embeddings=True)
        return embeddings.tolist()

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
            Tuple of (context_text, node_data)
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

        return context_text, node_data

    async def embed_node(
        self, node_id: str, ontology_id: int | None = None
    ) -> dict[str, Any]:
        """
        Embed a single node and update it in Neo4j.

        Args:
            node_id: Neo4j node ID (entity_instance_id)
            ontology_id: Optional ontology ID filter

        Returns:
            Dict with embedding info
        """
        context_text, node_data = await self.fetch_and_build_context(
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
        MATCH (n:EntityInstance)
        WHERE n.ontology_id = $ontology_id
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
            MATCH (e:EntityInstance {entity_instance_id: eid})
            RETURN e.entity_instance_id AS entity_id,
                   coalesce(e.text, "") AS text,
                   coalesce(e.autogenerated_text, "") AS autogenerated_text
            """
            fetch_res = await self.graph_session.run(fetch_query, ids=batch)
            fetch_rows = await fetch_res.data()

            # Prepare chunks per entity
            rows_to_create: list[dict[str, Any]] = []
            import uuid

            for row in fetch_rows:
                eid = row["entity_id"]
                raw_text = (row.get("text") or "").strip()
                raw_auto = (row.get("autogenerated_text") or "").strip()
                chunks = self._chunk_text(raw_text) or self._chunk_text(raw_auto)
                if not chunks:
                    continue
                # For now, drop prior chunks and recreate
                await self.graph_session.run(
                    """
                    MATCH (:EntityInstance {entity_instance_id: $eid})-[:HAS_CHUNK]->(c:EntityChunk)
                    DETACH DELETE c
                    """,
                    eid=eid,
                )
                # Embed chunks in batches
                loop = asyncio.get_event_loop()
                chunk_embeddings = await loop.run_in_executor(
                    None, self.embed_texts, chunks
                )
                for idx, (txt, emb) in enumerate(zip(chunks, chunk_embeddings)):
                    rows_to_create.append(
                        {
                            "entity_id": eid,
                            "chunk_id": str(uuid.uuid4()),
                            "chunk_index": idx,
                            "text": txt,
                            "embedding": emb,
                        }
                    )

            if not rows_to_create:
                continue

            # Create chunk nodes and link
            create_query = """
            UNWIND $rows AS row
            MATCH (e:EntityInstance {entity_instance_id: row.entity_id})
            CREATE (c:EntityChunk {
                chunk_id: row.chunk_id,
                parent_entity_instance_id: row.entity_id,
                instance_id: e.instance_id,
                ontology_id: e.ontology_id,
                chunk_index: row.chunk_index,
                text_chunk: row.text,
                text_embedding: row.embedding,
                text_embedding_model: $model_id,
                text_embedding_dim: $embed_dim,
                is_embedded: true,
                last_embedded_date: datetime()
            })
            CREATE (e)-[:HAS_CHUNK]->(c)
            SET e.is_embedded = true, e.last_embedded_date = datetime()
            """

            result = await self.graph_session.run(
                create_query,
                rows=rows_to_create,
                model_id=self.model_id,
                embed_dim=self.embed_dim,
            )
            await result.consume()

            nodes_processed += len(fetch_rows)

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
                RETURN e.entity_instance_id AS entity_id,
                       coalesce(e.text, "") AS text,
                       coalesce(e.autogenerated_text, "") AS autogenerated_text
                """
                fetch_res = await self.graph_session.run(fetch_query, ids=batch)
                fetch_rows = await fetch_res.data()
                rows_to_create: list[dict[str, Any]] = []
                import uuid
                for row in fetch_rows:
                    eid = row["entity_id"]
                    raw_text = (row.get("text") or "").strip()
                    raw_auto = (row.get("autogenerated_text") or "").strip()
                    chunks = self._chunk_text(raw_text) or self._chunk_text(raw_auto)
                    if not chunks:
                        continue
                    await self.graph_session.run(
                        """
                        MATCH (:EntityInstance {entity_instance_id: $eid})-[:HAS_CHUNK]->(c:EntityChunk)
                        DETACH DELETE c
                        """,
                        eid=eid,
                    )
                    loop = asyncio.get_event_loop()
                    embs = await loop.run_in_executor(None, self.embed_texts, chunks)
                    for idx, (txt, emb) in enumerate(zip(chunks, embs)):
                        rows_to_create.append(
                            {
                                "entity_id": eid,
                                "chunk_id": str(uuid.uuid4()),
                                "chunk_index": idx,
                                "text": txt,
                                "embedding": emb,
                            }
                        )
                if not rows_to_create:
                    continue
                create_query = """
                UNWIND $rows AS row
                MATCH (e:EntityInstance {entity_instance_id: row.entity_id})
                CREATE (c:EntityChunk {
                    chunk_id: row.chunk_id,
                    parent_entity_instance_id: row.entity_id,
                    instance_id: e.instance_id,
                    ontology_id: e.ontology_id,
                    chunk_index: row.chunk_index,
                    text_chunk: row.text,
                    text_embedding: row.embedding,
                    text_embedding_model: $model_id,
                    text_embedding_dim: $embed_dim,
                    is_embedded: true,
                    last_embedded_date: datetime()
                })
                CREATE (e)-[:HAS_CHUNK]->(c)
                """
                cres = await self.graph_session.run(
                    create_query,
                    rows=rows_to_create,
                    model_id=self.model_id,
                    embed_dim=self.embed_dim,
                )
                await cres.consume()
                processed += len(fetch_rows)
            except Exception:
                failed += len(batch)
                continue
        return {"ontology_id": ontology_id, "nodes_processed": processed, "nodes_failed": failed}

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

    async def ensure_chunk_vector_index(self, index_name: str = "entity_chunk_vec_idx") -> bool:
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
