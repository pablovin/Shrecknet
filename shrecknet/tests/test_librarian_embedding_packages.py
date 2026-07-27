from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from app.services.librarian_embedding_package_service import (
    EmbeddingPackageError,
    LibrarianEmbeddingPackageService,
    PACKAGE_FORMAT,
    PACKAGE_VERSION,
)
import app.services.librarian_embedding_package_service as package_module


def _graph() -> dict:
    common = {"ingestion_id": "old", "library_item_id": 7, "ontology_id": 3}
    return {
        "documents": [{**common, "book_title": "Source book", "is_active": True}],
        "pages": [{**common, "page_id": "page-1", "physical_page_number": 1}],
        "sections": [{**common, "section_id": "section-1", "parent_section_id": None}],
        "blocks": [{**common, "block_id": "block-1", "section_id": "section-1", "page_numbers": [1]}],
        "chunks": [
            {
                **common, "chunk_id": "parent-1", "chunk_role": "parent",
                "parent_chunk_id": None, "parent_section_id": "section-1",
                "source_block_ids": ["block-1"],
            },
            {
                **common, "chunk_id": "child-1", "chunk_role": "child",
                "parent_chunk_id": "parent-1", "parent_section_id": "section-1",
                "source_block_ids": ["block-1"], "text_embedding": [0.1, 0.2],
                "text_embedding_dim": 2, "text_embedding_model": "test-model",
            },
        ],
    }


def _package(graph: dict) -> bytes:
    graph_bytes = json.dumps(graph, separators=(",", ":")).encode()
    manifest = {
        "format": PACKAGE_FORMAT,
        "format_version": PACKAGE_VERSION,
        "source": {"library_item_id": 7, "ontology_id": 3},
        "embedding": {"model_id": "test-model", "dimension": 2},
        "counts": {key: len(value) for key, value in graph.items()},
        "graph_sha256": hashlib.sha256(graph_bytes).hexdigest(),
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("graph.json", graph_bytes)
    return output.getvalue()


def test_remap_uses_destination_placement_and_preserves_embedding() -> None:
    graph = _graph()
    remapped, ingestion_id = LibrarianEmbeddingPackageService._remap_graph(
        graph, library_item_id=99, ontology_id=42
    )
    child = next(row for row in remapped["chunks"] if row["chunk_role"] == "child")
    parent = next(row for row in remapped["chunks"] if row["chunk_role"] == "parent")
    assert child["library_item_id"] == 99
    assert child["ontology_id"] == 42
    assert child["ingestion_id"] == ingestion_id
    assert child["text_embedding"] == [0.1, 0.2]
    assert child["parent_chunk_id"] == parent["chunk_id"]
    assert child["source_block_ids"] == [remapped["blocks"][0]["block_id"]]
    assert child["chunk_id"] != "child-1"


def test_parse_rejects_checksum_tampering() -> None:
    package = _package(_graph())
    source = zipfile.ZipFile(io.BytesIO(package))
    manifest = json.loads(source.read("manifest.json"))
    manifest["graph_sha256"] = "0" * 64
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("graph.json", source.read("graph.json"))
    with pytest.raises(EmbeddingPackageError, match="checksum"):
        LibrarianEmbeddingPackageService.parse_package(output.getvalue())


def test_validation_rejects_non_finite_vectors() -> None:
    graph = _graph()
    graph["chunks"][1]["text_embedding"] = [0.1, float("nan")]
    with pytest.raises(EmbeddingPackageError, match="invalid child vector"):
        LibrarianEmbeddingPackageService._validate_graph(graph, check_runtime=False)


@pytest.mark.asyncio
async def test_graph_writes_are_split_into_bounded_transactions(monkeypatch) -> None:
    captured: list[list[dict]] = []

    class Transaction:
        async def run(self, _cypher, **params):
            captured.append(params["rows"])

    class Session:
        async def execute_write(self, callback):
            await callback(Transaction())

    monkeypatch.setattr(package_module, "IMPORT_BATCH_SIZE", 2)
    service = LibrarianEmbeddingPackageService(Session())
    rows = [{"id": index} for index in range(5)]

    await service._write_batches("UNWIND $rows AS row RETURN row", rows)

    assert captured == [rows[:2], rows[2:4], rows[4:]]
