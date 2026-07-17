"""V2 ontology-aware semantic document orchestration and Neo4j persistence."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import math
from typing import Any, Iterable

from neo4j import AsyncSession as AsyncNeo4jSession
from sqlalchemy.orm import Session

from app.core.config_store import get_settings
from app.graphrag.embedding_service import EmbeddingService
from app.graphrag.semantic_v2.chunking import LosslessTokenChunker
from app.graphrag.semantic_v2.documents import SemanticDocument
from app.graphrag.semantic_v2.renderers import SemanticDocumentRenderer
from app.models.ontology import OntologyEntity


VECTOR_INDEX = "semantic_document_vec_idx"
FULLTEXT_INDEX = "semantic_document_fulltext_idx"


def load_ontology_definitions(sql_session: Session, ontology_id: int) -> dict[int, dict[str, Any]]:
    """Load the SQL ontology vocabulary into renderer-friendly primitives."""
    entities = sql_session.query(OntologyEntity).filter(OntologyEntity.ontology_id == ontology_id).all()
    definitions: dict[int, dict[str, Any]] = {}
    for entity in entities:
        definitions[int(entity.id)] = {
            "id": int(entity.id), "ontology_id": int(ontology_id), "name": entity.name,
            "description": entity.description,
            "properties": [
                {
                    "id": int(prop.id), "name": prop.name, "description": prop.description,
                    "data_type": getattr(prop.data_type, "value", prop.data_type),
                    "cardinality": getattr(prop.cardinality, "value", prop.cardinality),
                }
                for prop in entity.properties
            ],
            "relationships": [],
        }
    for entity in entities:
        source = definitions[int(entity.id)]
        for rel in entity.relationships:
            destination = definitions.get(int(rel.destiny_entity_id)) if rel.destiny_entity_id else None
            source["relationships"].append({
                "id": int(rel.id), "ontology_id": int(ontology_id), "entity_id": int(entity.id),
                "name": rel.name, "description": rel.description,
                "source_name": entity.name,
                "destination_name": destination["name"] if destination else None,
                "destination_entity_id": int(rel.destiny_entity_id) if rel.destiny_entity_id else None,
                "bi_directional": bool(rel.bi_directional),
            })
    return definitions


class SemanticEmbeddingService:
    def __init__(self, graph_session: AsyncNeo4jSession, sql_session: Session) -> None:
        self.graph_session = graph_session
        self.sql_session = sql_session
        settings = get_settings()
        self.settings = settings
        self.embedding = EmbeddingService(graph_session)
        self.chunker = LosslessTokenChunker(
            target_tokens=settings.semantic_embedding_chunk_target_tokens,
            overlap_tokens=settings.semantic_embedding_chunk_overlap_tokens,
        )
        self.renderer = SemanticDocumentRenderer(
            self.chunker,
            long_text_threshold=settings.semantic_embedding_long_text_threshold_tokens,
        )

    async def ensure_indexes(self) -> None:
        await self.graph_session.run(
            "CREATE CONSTRAINT semantic_document_id_unique IF NOT EXISTS "
            "FOR (document:SemanticDocument) REQUIRE document.document_id IS UNIQUE"
        )
        await self.graph_session.run(
            f"CREATE VECTOR INDEX {VECTOR_INDEX} IF NOT EXISTS FOR (document:SemanticDocument) "
            "ON (document.text_embedding) OPTIONS {indexConfig: {"
            f"`vector.dimensions`: {int(self.settings.embedding_dimension)}, "
            "`vector.similarity_function`: 'cosine'}}"
        )
        await self.graph_session.run(
            f"CREATE FULLTEXT INDEX {FULLTEXT_INDEX} IF NOT EXISTS "
            "FOR (document:SemanticDocument) ON EACH [document.display_text, document.embedding_text]"
        )

    async def _definitions(self, ontology_id: int) -> dict[int, dict[str, Any]]:
        return await asyncio.to_thread(load_ontology_definitions, self.sql_session, ontology_id)

    async def _graph_sources(self, ontology_id: int, node_ids: list[str] | None = None) -> dict[str, list[dict[str, Any]]]:
        id_filter = "AND (e.entity_instance_id IN $node_ids)" if node_ids is not None else ""
        result = await self.graph_session.run(
            f"""
            MATCH (e:EntityInstance)
            WHERE toInteger(e.ontology_id) = toInteger($ontology_id) {id_filter}
            RETURN properties(e) AS node
            """, ontology_id=ontology_id, node_ids=node_ids or []
        )
        entities = [row["node"] for row in await result.data()]

        scene_filter = "AND scene.id IN $node_ids" if node_ids is not None else ""
        result = await self.graph_session.run(
            f"""
            MATCH (scene:Scene)
            WHERE toInteger(scene.ontology_id) = toInteger($ontology_id) {scene_filter}
            OPTIONAL MATCH (scene)-[:DERIVED_FROM]->(derived:EntityInstance)
            OPTIONAL MATCH (scene)-[:RELATES_TO]->(related:EntityInstance)
            RETURN properties(scene) AS node, derived.entity_instance_id AS derived_id,
                   derived.alias AS derived_alias, derived.entity_definition_id AS derived_definition_id,
                   collect(DISTINCT {{id: related.entity_instance_id, alias: related.alias}}) AS related_entities
            """, ontology_id=ontology_id, node_ids=node_ids or []
        )
        scenes = []
        for row in await result.data():
            node = dict(row["node"])
            node.update({key: row.get(key) for key in ("derived_id", "derived_alias", "derived_definition_id")})
            node["related_entities"] = [item for item in row.get("related_entities") or [] if item.get("id")]
            scenes.append(node)

        milestone_filter = "AND milestone.id IN $node_ids" if node_ids is not None else ""
        result = await self.graph_session.run(
            f"""
            MATCH (scene:Scene)-[:CONTAINS]->(milestone:Milestone)
            WHERE toInteger(milestone.ontology_id) = toInteger($ontology_id) {milestone_filter}
            OPTIONAL MATCH (milestone)-[:DERIVED_FROM]->(derived:EntityInstance)
            OPTIONAL MATCH (milestone)-[:RELATES_TO]->(related:EntityInstance)
            RETURN properties(milestone) AS node, scene.name AS scene_name,
                   derived.entity_instance_id AS derived_id,
                   collect(DISTINCT {{id: related.entity_instance_id, alias: related.alias}}) AS related_entities
            """, ontology_id=ontology_id, node_ids=node_ids or []
        )
        milestones = []
        for row in await result.data():
            node = dict(row["node"])
            node["scene_name"] = row.get("scene_name")
            node["derived_id"] = row.get("derived_id")
            node["related_entities"] = [item for item in row.get("related_entities") or [] if item.get("id")]
            milestones.append(node)
        return {"entities": entities, "scenes": scenes, "milestones": milestones}

    def _render_sources(
        self, ontology_id: int, definitions: dict[int, dict[str, Any]],
        sources: dict[str, list[dict[str, Any]]], *, include_vocabulary: bool,
    ) -> list[SemanticDocument]:
        documents: list[SemanticDocument] = []
        if include_vocabulary:
            for definition in definitions.values():
                documents.append(self.renderer.ontology_entity_definition(definition))
                documents.extend(
                    self.renderer.ontology_relationship_definition(rel)
                    for rel in definition.get("relationships") or []
                )
        for node in sources["entities"]:
            definition = definitions.get(int(node.get("entity_definition_id") or 0))
            if definition:
                documents.extend(self.renderer.entity(node, definition))
        for node in sources["scenes"]:
            derived_definition = definitions.get(int(node.get("derived_definition_id") or 0))
            node["derived_type_name"] = derived_definition.get("name") if derived_definition else None
            documents.append(self.renderer.scene(node))
        documents.extend(self.renderer.milestone(node) for node in sources["milestones"])
        return self._bounded_documents(documents)

    def _bounded_documents(self, documents: list[SemanticDocument]) -> list[SemanticDocument]:
        """Guarantee no V2 document relies on transformer truncation."""
        bounded: list[SemanticDocument] = []
        for document in documents:
            if self.chunker.token_count(document.embedding_text) <= self.chunker.target_tokens:
                bounded.append(document)
                continue
            parts = self.chunker.split(document.display_text)
            for index, part in enumerate(parts):
                bounded.append(replace(
                    document,
                    document_id=f"{document.document_id}:part:{index}",
                    display_text=part,
                    embedding_text=f"passage: {part}",
                    chunk_index=index,
                    chunk_count=len(parts),
                ))
        return bounded

    async def _existing(self, ontology_id: int, document_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not document_ids:
            return {}
        result = await self.graph_session.run(
            """
            MATCH (document:SemanticDocument)
            WHERE toInteger(document.ontology_id) = toInteger($ontology_id)
              AND document.document_id IN $document_ids
            RETURN document.document_id AS document_id, document.content_hash AS content_hash,
                   document.metadata_hash AS metadata_hash, document.embedding_version AS embedding_version,
                   document.text_embedding_model AS model, document.text_embedding_dim AS dimension
            """, ontology_id=ontology_id, document_ids=document_ids
        )
        return {row["document_id"]: row for row in await result.data()}

    async def reconcile_documents(self, documents: list[SemanticDocument]) -> dict[str, int]:
        if not documents:
            return {"documents_requested": 0, "documents_embedded": 0, "documents_reused": 0}
        ontology_id = documents[0].ontology_id
        existing = await self._existing(ontology_id, [doc.document_id for doc in documents])
        version = self.settings.semantic_embedding_version
        model = self.settings.embedding_model_id
        dimension = int(self.settings.embedding_dimension)
        changed = [
            doc for doc in documents
            if not (existing.get(doc.document_id)
                and existing[doc.document_id].get("content_hash") == doc.content_hash
                and existing[doc.document_id].get("embedding_version") == version
                and existing[doc.document_id].get("model") == model
                and int(existing[doc.document_id].get("dimension") or 0) == dimension)
        ]
        vectors: dict[str, list[float]] = {}
        if changed:
            oversized = [
                doc.document_id for doc in changed
                if self.chunker.token_count(doc.embedding_text) > self.chunker.target_tokens
            ]
            if oversized:
                raise ValueError(f"Oversized semantic documents reached inference: {oversized[:5]}")
            embedded = await asyncio.to_thread(
                self.embedding.embed_texts, [doc.embedding_text for doc in changed]
            )
            for doc, vector in zip(changed, embedded):
                normalized = [float(value) for value in vector]
                if len(normalized) != dimension or not all(math.isfinite(value) for value in normalized):
                    raise ValueError(f"Invalid embedding for semantic document {doc.document_id}")
                vectors[doc.document_id] = normalized

        rows = []
        for doc in documents:
            rows.append({
                "document_id": doc.document_id, "source_kind": doc.source_kind,
                "source_node_id": doc.source_node_id, "ontology_id": doc.ontology_id,
                "instance_id": doc.instance_id, "entity_definition_id": doc.entity_definition_id,
                "relationship_definition_id": doc.relationship_definition_id, "scene_id": doc.scene_id,
                "source_field": doc.source_field, "source_text_hash": doc.source_text_hash,
                "chunk_index": doc.chunk_index, "chunk_count": doc.chunk_count,
                "related_entity_ids": doc.related_entity_ids,
                "derived_from_entity_id": doc.derived_from_entity_id,
                "source_created_at": doc.source_created_at, "source_updated_at": doc.source_updated_at,
                "display_text": doc.display_text, "embedding_text": doc.embedding_text,
                "text_chunk": doc.display_text, "chunk_type": doc.source_kind,
                "parent_entity_instance_id": doc.source_node_id,
                "content_hash": doc.content_hash, "metadata_hash": doc.metadata_hash,
                "vector": vectors.get(doc.document_id),
            })
        tx = await self.graph_session.begin_transaction()
        try:
            await tx.run(
                """
                UNWIND $rows AS row
                MERGE (document:SemanticDocument {document_id: row.document_id})
                SET document += row,
                    document.embedding_version = $version,
                    document.text_embedding_model = $model,
                    document.text_embedding_dim = $dimension,
                    document.updated_at = datetime(),
                    document.created_at = coalesce(document.created_at, datetime())
                FOREACH (_ IN CASE WHEN row.vector IS NULL THEN [] ELSE [1] END |
                    SET document.text_embedding = row.vector,
                        document.last_embedded_date = datetime()
                )
                REMOVE document.vector
                WITH document, row
                OPTIONAL MATCH (source)
                WHERE row.source_node_id IS NOT NULL AND (
                    (source:EntityInstance AND source.entity_instance_id = row.source_node_id) OR
                    ((source:Scene OR source:Milestone) AND source.id = row.source_node_id)
                )
                FOREACH (_ IN CASE WHEN source IS NULL THEN [] ELSE [1] END |
                    MERGE (source)-[:HAS_SEMANTIC_DOCUMENT]->(document)
                )
                """, rows=rows, version=version, model=model, dimension=dimension
            )
            source_ids = sorted({doc.source_node_id for doc in documents if doc.source_node_id})
            if source_ids:
                await tx.run(
                    """
                    MATCH (obsolete:SemanticDocument)
                    WHERE toInteger(obsolete.ontology_id) = toInteger($ontology_id)
                      AND obsolete.source_node_id IN $source_ids
                      AND NOT obsolete.document_id IN $desired_ids
                    DETACH DELETE obsolete
                    """,
                    ontology_id=ontology_id,
                    source_ids=source_ids,
                    desired_ids=[doc.document_id for doc in documents],
                )
            await tx.commit()
        except Exception:
            await tx.rollback()
            raise
        finally:
            await tx.close()
        return {
            "documents_requested": len(documents), "documents_embedded": len(changed),
            "documents_reused": len(documents) - len(changed),
        }

    async def embed_ontology(self, ontology_id: int, *, batch_size: int = 50) -> dict[str, Any]:
        await self.ensure_indexes()
        definitions, sources = await asyncio.gather(
            self._definitions(ontology_id), self._graph_sources(ontology_id)
        )
        documents = self._render_sources(ontology_id, definitions, sources, include_vocabulary=True)
        totals = {"documents_requested": 0, "documents_embedded": 0, "documents_reused": 0}
        groups: dict[str, list[SemanticDocument]] = {}
        for document in documents:
            group_key = document.source_node_id or (
                f"{document.source_kind}:{document.entity_definition_id}:"
                f"{document.relationship_definition_id or ''}"
            )
            groups.setdefault(group_key, []).append(document)
        for group in groups.values():
            result = await self.reconcile_documents(group)
            for key in totals:
                totals[key] += int(result[key])
        desired_ids = [doc.document_id for doc in documents]
        await self.graph_session.run(
            """
            MATCH (document:SemanticDocument)
            WHERE toInteger(document.ontology_id) = toInteger($ontology_id)
              AND NOT document.document_id IN $desired_ids
            DETACH DELETE document
            """, ontology_id=ontology_id, desired_ids=desired_ids
        )
        await self._refresh_source_flags(ontology_id)
        return {
            "ontology_id": ontology_id, "nodes_processed": len(sources["entities"]) + len(sources["scenes"]) + len(sources["milestones"]),
            "nodes_failed": 0,
            "processed_by_type": {"entities": len(sources["entities"]), "scenes": len(sources["scenes"]), "milestones": len(sources["milestones"])},
            **totals,
        }

    async def embed_nodes(self, ontology_id: int, node_ids: Iterable[str]) -> dict[str, Any]:
        ids = sorted({str(value) for value in node_ids if value})
        if not ids:
            return {"nodes_requested": 0, "nodes_embedded": 0, "nodes_failed": 0, "nodes_skipped": 0, "missing_nodes": []}
        definitions, sources = await asyncio.gather(
            self._definitions(ontology_id), self._graph_sources(ontology_id, ids)
        )
        documents = self._render_sources(ontology_id, definitions, sources, include_vocabulary=False)
        result = await self.reconcile_documents(documents)
        found = {doc.source_node_id for doc in documents if doc.source_node_id}
        missing = [node_id for node_id in ids if node_id not in found]
        if missing:
            await self.graph_session.run(
                """
                MATCH (document:SemanticDocument)
                WHERE toInteger(document.ontology_id) = toInteger($ontology_id)
                  AND document.source_node_id IN $missing
                DETACH DELETE document
                """,
                ontology_id=ontology_id,
                missing=missing,
            )
        await self._refresh_source_flags(ontology_id, list(found))
        return {"nodes_requested": len(ids), "nodes_embedded": len(found), "nodes_failed": 0, "nodes_skipped": len(missing), "missing_nodes": missing, **result}

    async def embed_instance(self, instance_id: str) -> dict[str, Any]:
        result = await self.graph_session.run(
            "MATCH (instance:OntologyInstance {instance_id: $instance_id}) RETURN instance.ontology_id AS ontology_id",
            instance_id=instance_id,
        )
        row = await result.single()
        if not row:
            raise ValueError(f"Ontology instance {instance_id} not found")
        ontology_id = int(row["ontology_id"])
        definitions = await self._definitions(ontology_id)
        sources = await self._graph_sources(ontology_id)
        sources = {key: [node for node in values if str(node.get("instance_id")) == instance_id] for key, values in sources.items()}
        documents = self._render_sources(ontology_id, definitions, sources, include_vocabulary=False)
        outcome = await self.reconcile_documents(documents)
        await self._refresh_source_flags(ontology_id, [doc.source_node_id for doc in documents if doc.source_node_id])
        return {"instance_id": instance_id, "ontology_id": ontology_id, "nodes_requested": sum(len(v) for v in sources.values()), "nodes_embedded": len({d.source_node_id for d in documents}), "nodes_failed": 0, "nodes_skipped": 0, **outcome}

    async def embed_definitions(self, ontology_id: int, definition_ids: Iterable[int]) -> dict[str, Any]:
        ids = sorted({int(value) for value in definition_ids})
        definitions, sources = await asyncio.gather(
            self._definitions(ontology_id), self._graph_sources(ontology_id)
        )
        selected = {key: value for key, value in definitions.items() if key in ids}
        sources["entities"] = [
            node for node in sources["entities"]
            if int(node.get("entity_definition_id") or 0) in ids
        ]
        sources["scenes"] = []
        sources["milestones"] = []
        documents = self._render_sources(ontology_id, selected, sources, include_vocabulary=True)
        outcome = await self.reconcile_documents(documents)
        desired = [document.document_id for document in documents]
        await self.graph_session.run(
            """
            MATCH (document:SemanticDocument)
            WHERE toInteger(document.ontology_id) = toInteger($ontology_id)
              AND toInteger(document.entity_definition_id) IN $definition_ids
              AND NOT document.document_id IN $desired
            DETACH DELETE document
            """,
            ontology_id=ontology_id, definition_ids=ids, desired=desired,
        )
        await self._refresh_source_flags(
            ontology_id,
            [document.source_node_id for document in documents if document.source_node_id],
        )
        return {"ontology_id": ontology_id, "definition_ids": ids, **outcome}

    async def _refresh_source_flags(self, ontology_id: int, source_ids: list[str] | None = None) -> None:
        await self.graph_session.run(
            """
            MATCH (source)
            WHERE (source:EntityInstance OR source:Scene OR source:Milestone)
              AND toInteger(source.ontology_id) = toInteger($ontology_id)
              AND ($source_ids IS NULL OR coalesce(source.entity_instance_id, source.id) IN $source_ids)
            OPTIONAL MATCH (source)-[:HAS_SEMANTIC_DOCUMENT]->(document:SemanticDocument)
            WITH source, count(document) AS document_count, max(document.last_embedded_date) AS embedded_at
            SET source.is_embedded = document_count > 0, source.last_embedded_date = embedded_at
            """, ontology_id=ontology_id, source_ids=source_ids
        )

    async def reset_ontology(self, ontology_id: int) -> dict[str, int]:
        result = await self.graph_session.run(
            """
            MATCH (document:SemanticDocument)
            WHERE true
              AND toInteger(document.ontology_id) = toInteger($ontology_id)
            WITH collect(document) AS documents
            CALL (documents) {
                UNWIND documents AS document
                DETACH DELETE document
            }
            RETURN size(documents) AS deleted
            """, ontology_id=ontology_id
        )
        row = await result.single()
        deleted = int(row["deleted"] if row else 0)
        result = await self.graph_session.run(
            """
            MATCH (source)
            WHERE (source:EntityInstance OR source:Scene OR source:Milestone OR source:Event)
              AND toInteger(source.ontology_id) = toInteger($ontology_id)
            SET source.is_embedded = false, source.last_embedded_date = null
            REMOVE source.text_embedding, source.text_embedding_model, source.text_embedding_dim, source.context_text
            RETURN count(source) AS reset
            """, ontology_id=ontology_id
        )
        row = await result.single()
        return {"ontology_id": ontology_id, "nodes_reset": int(row["reset"] if row else 0), "orphans_deleted": 0, "chunks_deleted": deleted}
