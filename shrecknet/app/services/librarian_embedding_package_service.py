"""Portable export/import packages for Librarian book embeddings."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import zipfile
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.core.config_store import get_settings


logger = logging.getLogger(__name__)


PACKAGE_FORMAT = "shrecknet-librarian-embedding"
PACKAGE_VERSION = 1
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
NODE_GROUPS = ("documents", "pages", "sections", "blocks", "chunks")
IMPORT_BATCH_SIZE = 500


class EmbeddingPackageError(ValueError):
    """The uploaded embedding package is malformed or incompatible."""


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode("utf-8")


class LibrarianEmbeddingPackageService:
    def __init__(self, graph_session: Any):
        self.graph_session = graph_session

    async def export_package(self, library_item_id: int, ontology_id: int) -> bytes:
        document_result = await self.graph_session.run(
            """
            MATCH (d:PdfDocument {library_item_id: $item_id, is_active: true})
            RETURN properties(d) AS properties
            ORDER BY d.activated_at DESC LIMIT 1
            """,
            item_id=library_item_id,
        )
        document_record = await document_result.single()
        if not document_record:
            raise EmbeddingPackageError("The library item has no active structured embedding")
        document = dict(document_record["properties"])
        ingestion_id = document.get("ingestion_id")
        if not ingestion_id:
            raise EmbeddingPackageError("The active embedding has no ingestion identifier")

        graph: dict[str, list[dict[str, Any]]] = {"documents": [document]}
        label_groups = {
            "pages": "PdfPage",
            "sections": "PdfSection",
            "blocks": "PdfBlock",
            "chunks": "PdfChunk",
        }
        for group, label in label_groups.items():
            result = await self.graph_session.run(
                f"MATCH (n:{label} {{ingestion_id: $ingestion_id}}) RETURN properties(n) AS properties",
                ingestion_id=ingestion_id,
            )
            graph[group] = [dict(row["properties"]) async for row in result]

        self._validate_graph(graph, check_runtime=False)
        child = next(c for c in graph["chunks"] if c.get("chunk_role") == "child")
        graph_bytes = _json_bytes(graph)
        manifest = {
            "format": PACKAGE_FORMAT,
            "format_version": PACKAGE_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": {
                "library_item_id": library_item_id,
                "ontology_id": ontology_id,
                "book_title": document.get("book_title"),
                "source_sha256": document.get("source_sha256"),
            },
            "embedding": {
                "model_id": child.get("text_embedding_model"),
                "dimension": child.get("text_embedding_dim"),
                "version": child.get("embedding_version"),
            },
            "counts": {group: len(graph[group]) for group in NODE_GROUPS},
            "graph_sha256": hashlib.sha256(graph_bytes).hexdigest(),
        }
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", _json_bytes(manifest))
            archive.writestr("graph.json", graph_bytes)
        return output.getvalue()

    async def import_package(
        self, package_bytes: bytes, *, library_item_id: int, ontology_id: int
    ) -> dict[str, Any]:
        manifest, graph = self.parse_package(package_bytes)
        remapped, ingestion_id = self._remap_graph(
            graph, library_item_id=library_item_id, ontology_id=ontology_id
        )
        await self._stage_graph(remapped, library_item_id, ontology_id, ingestion_id)
        try:
            await self._validate_staged_graph(ingestion_id)
            previous_ingestion_id = await self._activate_staged_graph(
                library_item_id, ingestion_id
            )
        except Exception:
            # Staging is deliberately committed in small batches. A failed
            # import must not leave an inactive graph consuming storage or
            # interfere with a later retry.
            await self._delete_ingestion_graph(ingestion_id)
            raise
        if previous_ingestion_id:
            try:
                await self._delete_ingestion_graph(previous_ingestion_id)
            except Exception:
                # The new graph is already active. Retired-graph cleanup is
                # safe to retry and must not report the completed import as a
                # failure.
                logger.exception(
                    "librarian_embedding_import_cleanup_failed ingestion_id=%s",
                    previous_ingestion_id,
                )
        return {
            "library_item_id": library_item_id,
            "ontology_id": ontology_id,
            "ingestion_id": ingestion_id,
            "source": manifest["source"],
            "embedding": manifest["embedding"],
            "counts": manifest["counts"],
        }

    @classmethod
    def parse_package(cls, package_bytes: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
        if not package_bytes:
            raise EmbeddingPackageError("The embedding package is empty")
        if len(package_bytes) > MAX_PACKAGE_BYTES:
            raise EmbeddingPackageError("The embedding package exceeds the 512 MiB limit")
        try:
            with zipfile.ZipFile(io.BytesIO(package_bytes)) as archive:
                names = set(archive.namelist())
                if names != {"manifest.json", "graph.json"}:
                    raise EmbeddingPackageError("Package must contain only manifest.json and graph.json")
                if sum(info.file_size for info in archive.infolist()) > MAX_PACKAGE_BYTES:
                    raise EmbeddingPackageError("The uncompressed embedding package is too large")
                manifest = json.loads(archive.read("manifest.json"))
                graph_bytes = archive.read("graph.json")
                graph = json.loads(graph_bytes)
        except EmbeddingPackageError:
            raise
        except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
            raise EmbeddingPackageError("Invalid Librarian embedding package") from exc
        if manifest.get("format") != PACKAGE_FORMAT or manifest.get("format_version") != PACKAGE_VERSION:
            raise EmbeddingPackageError("Unsupported Librarian embedding package format")
        if hashlib.sha256(graph_bytes).hexdigest() != manifest.get("graph_sha256"):
            raise EmbeddingPackageError("Embedding package checksum validation failed")
        cls._validate_graph(graph, check_runtime=True)
        actual_counts = {group: len(graph[group]) for group in NODE_GROUPS}
        if actual_counts != manifest.get("counts"):
            raise EmbeddingPackageError("Embedding package node counts do not match its manifest")
        child = next(row for row in graph["chunks"] if row.get("chunk_role") == "child")
        declared_embedding = manifest.get("embedding") or {}
        if (
            declared_embedding.get("model_id") != child.get("text_embedding_model")
            or declared_embedding.get("dimension") != child.get("text_embedding_dim")
            or declared_embedding.get("version") != child.get("embedding_version")
        ):
            raise EmbeddingPackageError("Embedding metadata does not match the packaged vectors")
        return manifest, graph

    @staticmethod
    def _validate_graph(graph: dict[str, Any], *, check_runtime: bool) -> None:
        if not isinstance(graph, dict) or any(not isinstance(graph.get(k), list) for k in NODE_GROUPS):
            raise EmbeddingPackageError("Embedding graph has an invalid structure")
        if len(graph["documents"]) != 1 or not graph["pages"] or not graph["sections"]:
            raise EmbeddingPackageError("Embedding graph is missing its document structure")
        sections = {row.get("section_id") for row in graph["sections"]}
        blocks = {row.get("block_id") for row in graph["blocks"]}
        chunks = {row.get("chunk_id"): row for row in graph["chunks"]}
        children = [row for row in graph["chunks"] if row.get("chunk_role") == "child"]
        if not children or None in sections or None in blocks or None in chunks:
            raise EmbeddingPackageError("Embedding graph has missing identifiers or no child chunks")
        if len(chunks) != len(graph["chunks"]):
            raise EmbeddingPackageError("Embedding graph contains duplicate chunk identifiers")
        model_ids: set[str] = set()
        dimensions: set[int] = set()
        for child in children:
            vector = child.get("text_embedding")
            dimension = child.get("text_embedding_dim")
            if (
                not isinstance(vector, list)
                or not isinstance(dimension, int)
                or len(vector) != dimension
                or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector)
            ):
                raise EmbeddingPackageError("Embedding graph contains an invalid child vector")
            if child.get("parent_chunk_id") not in chunks:
                raise EmbeddingPackageError("Embedding graph contains a child without its parent")
            if child.get("parent_section_id") not in sections:
                raise EmbeddingPackageError("Embedding graph contains a chunk without its section")
            if any(block_id not in blocks for block_id in child.get("source_block_ids", [])):
                raise EmbeddingPackageError("Embedding graph contains missing source blocks")
            model_ids.add(str(child.get("text_embedding_model")))
            dimensions.add(dimension)
        if len(model_ids) != 1 or len(dimensions) != 1:
            raise EmbeddingPackageError("Embedding graph mixes incompatible vector models")
        if check_runtime:
            settings = get_settings()
            if model_ids != {settings.embedding_model_id} or dimensions != {settings.embedding_dimension}:
                raise EmbeddingPackageError(
                    "Embedding model or dimension is incompatible with this Shrecknet instance"
                )

    @staticmethod
    def _remap_graph(
        graph: dict[str, Any], *, library_item_id: int, ontology_id: int
    ) -> tuple[dict[str, list[dict[str, Any]]], str]:
        ingestion_id = str(uuid4())
        id_fields = {
            "pages": "page_id", "sections": "section_id", "blocks": "block_id", "chunks": "chunk_id"
        }
        mappings: dict[str, dict[str, str]] = {}
        for group, field in id_fields.items():
            mappings[field] = {
                str(row[field]): str(uuid5(NAMESPACE_URL, f"shrecknet:import:{ingestion_id}:{group}:{row[field]}"))
                for row in graph[group]
            }
        remapped: dict[str, list[dict[str, Any]]] = {}
        for group in NODE_GROUPS:
            remapped[group] = []
            for original in graph[group]:
                row = dict(original)
                row.update(
                    ingestion_id=ingestion_id,
                    library_item_id=library_item_id,
                    ontology_id=ontology_id,
                    is_active=False,
                )
                identity_field = id_fields.get(group)
                if identity_field is not None:
                    row[identity_field] = mappings[identity_field][str(row[identity_field])]
                references: tuple[tuple[str, str], ...] = ()
                if group == "sections":
                    references = (("parent_section_id", "section_id"),)
                elif group == "blocks":
                    references = (("section_id", "section_id"),)
                elif group == "chunks":
                    references = (
                        ("parent_section_id", "section_id"),
                        ("parent_chunk_id", "chunk_id"),
                    )
                for field, mapping_name in references:
                    if row.get(field) is not None:
                        row[field] = mappings[mapping_name][str(row[field])]
                if "block_ids" in row:
                    row["block_ids"] = [mappings["block_id"][str(v)] for v in row["block_ids"]]
                if "source_block_ids" in row:
                    row["source_block_ids"] = [mappings["block_id"][str(v)] for v in row["source_block_ids"]]
                remapped[group].append(row)
        return remapped, ingestion_id

    @staticmethod
    def _batches(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        return [rows[index:index + IMPORT_BATCH_SIZE]
                for index in range(0, len(rows), IMPORT_BATCH_SIZE)]

    async def _write_batches(self, cypher: str, rows: list[dict[str, Any]], **params: Any) -> None:
        """Write independent graph rows in bounded transactions.

        A package can contain many thousands of vectors and relationships.
        Keeping all of those writes in one transaction made imports slow and
        prone to exhausting the destination Neo4j transaction memory.
        """
        for batch in self._batches(rows):
            async def write(tx: Any, batch: list[dict[str, Any]] = batch) -> None:
                await tx.run(cypher, rows=batch, **params)
            await self.graph_session.execute_write(write)

    async def _stage_graph(
        self, graph: dict[str, list[dict[str, Any]]], library_item_id: int,
        ontology_id: int, ingestion_id: str,
    ) -> None:
        await self._write_batches(
            "MERGE (li:LibraryItem {library_item_id: $item_id}) SET li.ontology_id = $ontology_id",
            [{}], item_id=library_item_id, ontology_id=ontology_id,
        )
        await self._write_batches(
            """UNWIND $rows AS row MATCH (li:LibraryItem {library_item_id: $item_id})
            CREATE (d:PdfDocument) SET d = row, d.is_active = false
            CREATE (li)-[:HAS_DOCUMENT]->(d)""", graph["documents"], item_id=library_item_id,
        )
        for group, label, relation in (("pages", "PdfPage", "HAS_PAGE"), ("sections", "PdfSection", "HAS_SECTION")):
            await self._write_batches(
                f"""UNWIND $rows AS row MATCH (d:PdfDocument {{ingestion_id: $ingestion_id}})
                CREATE (n:{label}) SET n = row CREATE (d)-[:{relation}]->(n)""",
                graph[group], ingestion_id=ingestion_id,
            )
        await self._write_batches(
            """UNWIND $rows AS row MATCH (s:PdfSection {section_id: row.section_id})
            CREATE (b:PdfBlock) SET b = row CREATE (s)-[:CONTAINS_BLOCK]->(b)""", graph["blocks"],
        )
        nested_sections = [row for row in graph["sections"] if row.get("parent_section_id")]
        await self._write_batches(
            """UNWIND $rows AS row MATCH (s:PdfSection {section_id: row.section_id}),
            (p:PdfSection {section_id: row.parent_section_id}) CREATE (p)-[:HAS_SUBSECTION]->(s)""", nested_sections,
        )
        ordered_blocks = sorted(graph["blocks"], key=lambda row: (int(row.get("reading_order", 0)), str(row["block_id"])))
        block_pairs = [{"left": left["block_id"], "right": right["block_id"]}
                       for left, right in zip(ordered_blocks, ordered_blocks[1:])]
        await self._write_batches(
            """UNWIND $rows AS row MATCH (a:PdfBlock {block_id: row.left}),
            (b:PdfBlock {block_id: row.right}) CREATE (a)-[:NEXT_BLOCK]->(b)""", block_pairs,
        )
        block_page_links = [{"block_id": row["block_id"], "page": page}
                            for row in graph["blocks"] for page in row.get("page_numbers", [])]
        await self._write_batches(
            """UNWIND $rows AS row MATCH (b:PdfBlock {block_id: row.block_id}),
            (p:PdfPage {ingestion_id: $ingestion_id, physical_page_number: row.page})
            CREATE (b)-[:ON_PAGE]->(p)""", block_page_links, ingestion_id=ingestion_id,
        )
        await self._write_batches(
            """UNWIND $rows AS row CREATE (c:PdfChunkRecord:PdfChunkCandidate) SET c = row,
            c.is_active = false""", graph["chunks"],
        )
        parent_chunks = [row for row in graph["chunks"] if row.get("chunk_role") == "parent"]
        await self._write_batches(
            """UNWIND $rows AS row MATCH (s:PdfSection {section_id: row.parent_section_id}),
            (c:PdfChunkCandidate {chunk_id: row.chunk_id}) CREATE (s)-[:HAS_PARENT_CHUNK]->(c)""", parent_chunks,
        )
        children = [row for row in graph["chunks"] if row.get("chunk_role") == "child"]
        await self._write_batches(
            """UNWIND $rows AS row MATCH (c:PdfChunkCandidate {chunk_id: row.chunk_id}),
            (p:PdfChunkCandidate {chunk_id: row.parent_chunk_id}) CREATE (c)-[:CHILD_OF]->(p)""", children,
        )
        derivations = [{"chunk_id": row["chunk_id"], "block_id": block_id}
                       for row in graph["chunks"] for block_id in row.get("source_block_ids", [])]
        await self._write_batches(
            """UNWIND $rows AS row MATCH (c:PdfChunkCandidate {chunk_id: row.chunk_id}),
            (b:PdfBlock {block_id: row.block_id}) CREATE (c)-[:DERIVED_FROM]->(b)""", derivations,
        )
        page_links = [{"chunk_id": row["chunk_id"], "page": page}
                      for row in graph["chunks"] for page in row.get("physical_page_numbers", [])]
        await self._write_batches(
            """UNWIND $rows AS row MATCH (c:PdfChunkCandidate {chunk_id: row.chunk_id}),
            (p:PdfPage {ingestion_id: $ingestion_id, physical_page_number: row.page})
            CREATE (c)-[:ON_PAGE]->(p)""", page_links, ingestion_id=ingestion_id,
        )

    async def _validate_staged_graph(self, ingestion_id: str) -> None:
        result = await self.graph_session.run(
            """MATCH (child:PdfChunkCandidate {ingestion_id: $ingestion_id, chunk_role: 'child'})
            WITH count(child) AS children,
                 count(CASE WHEN size(child.text_embedding) = $dimension THEN 1 END) AS vectors
            WHERE children > 0 AND children = vectors RETURN children""",
            ingestion_id=ingestion_id, dimension=get_settings().embedding_dimension,
        )
        if not await result.single():
            raise EmbeddingPackageError("Imported graph failed staged vector validation")

    async def _activate_staged_graph(self, library_item_id: int, ingestion_id: str) -> str | None:
        async def activate(tx: Any) -> str | None:
            result = await tx.run(
                """OPTIONAL MATCH (old:PdfDocument {library_item_id: $item_id, is_active: true})
                WITH old, old.ingestion_id AS previous_id
                FOREACH (_ IN CASE WHEN old IS NULL THEN [] ELSE [1] END |
                  SET old.is_active = false, old.retired_at = datetime())
                WITH previous_id MATCH (new:PdfDocument {ingestion_id: $ingestion_id})
                SET new.is_active = true, new.activated_at = datetime()
                WITH previous_id MATCH (candidate:PdfChunkCandidate {ingestion_id: $ingestion_id})
                REMOVE candidate:PdfChunkCandidate SET candidate:PdfChunk, candidate.is_active = true
                RETURN previous_id""",
                item_id=library_item_id, ingestion_id=ingestion_id,
            )
            record = await result.single()
            return record["previous_id"] if record else None
        return await self.graph_session.execute_write(activate)

    async def _delete_ingestion_graph(self, ingestion_id: str) -> None:
        await self.graph_session.run(
            "MATCH (node {ingestion_id: $ingestion_id}) DETACH DELETE node",
            ingestion_id=ingestion_id,
        )
