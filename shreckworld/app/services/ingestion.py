"""Structured Docling/E5 ingestion with SQL activation and versioned Qdrant data."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from sqlalchemy import select
from sqlalchemy.orm import Session
from shreckdoc.pipeline import EMBEDDING_VERSION, _atomic_json, _sha256, _token_count, build_chunks

from app.models import ShreckWorld, ShreckWorldDocument, ShreckWorldDocumentChunk, ShreckWorldEmbeddingJob, ShreckWorldLibraryItem
from app.repositories.documents import activate_document
from app.schemas.shreckworld import IndexingJobMetrics
from app.services.document_parsing import parse_source, with_heartbeat
from app.services.embedding import get_embedder
from app.services.retrieval_index import upsert_item_chunks, delete_document_chunks

logger = logging.getLogger(__name__)


def embed_library_item(session: Session, *, library_item_id: str, job_id: str) -> None:
    job = session.get(ShreckWorldEmbeddingJob, job_id)
    item = session.get(ShreckWorldLibraryItem, library_item_id)
    if job is None or item is None or job.library_item_id != item.id or job.status in {"completed", "failed"}:
        return
    metrics = IndexingJobMetrics(phase="validating_model", progress_mode="indeterminate")
    now = datetime.now(timezone.utc)
    job.started_at, job.completed_at = now, None
    job.status, job.progress = "running", 1
    item.embedding_status, item.embedding_error = "embedding", None
    item.index_status, item.index_error = "parsing", None
    phase_started = last_checkpoint = monotonic()
    output_dir = Path(item.storage_path or item.pdf_path).parent / "parsed" / job.id
    attempted_projection = False

    def checkpoint(*, force: bool = False) -> None:
        nonlocal last_checkpoint
        current = monotonic()
        if force or current - last_checkpoint >= 1:
            metrics.phase_seconds[metrics.phase] = round(current - phase_started, 3)
            job.metrics_json = metrics.model_dump_json()
            job.updated_at = datetime.now(timezone.utc)
            session.commit()
            last_checkpoint = current

    def phase(name: str, progress: int, detail: str, *, indeterminate: bool = False) -> None:
        nonlocal phase_started
        checkpoint(force=True)
        metrics.phase = name
        metrics.progress_mode = "indeterminate" if indeterminate else "determinate"
        phase_started = monotonic()
        job.progress, job.detail = progress, detail
        checkpoint(force=True)

    checkpoint(force=True)
    try:
        embedder = get_embedder()
        health = embedder.ensure_ready()
        metrics.embedding_backend = "sentence_transformers"
        metrics.embedding_model = embedder.settings.embedding_model_id
        metrics.embedding_fingerprint = str(health["fingerprint"])
        metrics.embedding_revision = str(health["revision"])
        metrics.tokenizer_limit = embedder.token_limit
        path = Path(item.storage_path or item.pdf_path)
        if _sha256(path) != item.source_sha256:
            raise ValueError("Source hash changed after upload; refusing to index a different document")
        world = session.get(ShreckWorld, item.shreckworld_id)
        phase("parsing", 5, "Parsing structure with Docling; page progress is indeterminate", indeterminate=True)

        def parsing_heartbeat(total: int | None) -> None:
            metrics.total_pages = total
            checkpoint(force=True)

        parsed = parse_source(path, ingestion_id=job.id, output_dir=output_dir, heartbeat=parsing_heartbeat)
        normalized = parsed.document
        metrics.parser_name, metrics.parser_version = parsed.parser_name, parsed.parser_version
        metrics.parse_quality = parsed.quality
        metrics.warnings.extend(parsed.diagnostics)
        metrics.processed_pages = metrics.total_pages = len(normalized.pages)
        text_pages = {number for block in normalized.blocks if block.display_text.strip() and block.block_type not in {"page_header", "page_footer", "heading"} for number in block.page_numbers}
        metrics.text_pages, metrics.empty_pages = len(text_pages), len(normalized.pages) - len(text_pages)
        metrics.extracted_characters = sum(len(block.display_text) for block in normalized.blocks)
        metrics.sections, metrics.blocks = len(normalized.sections), len(normalized.blocks)
        if metrics.empty_pages:
            metrics.warnings.append(f"{metrics.empty_pages} pages yielded no retrievable text; OCR is not performed.")
        scope_id, book_title, system_name = item.shreckworld_id, item.title, world.name if world else ""
        ingestion_id, source_item_id = job.id, item.id
        phase("chunking", 35, "Building structural parents and tokenizer-bounded E5 children", indeterminate=True)
        chunks = with_heartbeat(lambda: build_chunks(
            document=normalized, ingestion_id=ingestion_id, library_item_id=source_item_id,
            ontology_id=scope_id, book_title=book_title, rpg_system=system_name,
            tokenizer=embedder.tokenizer, max_embedding_tokens=embedder.token_limit,
        ), lambda: checkpoint(force=True))
        children = [chunk for chunk in chunks if chunk.chunk_role == "child"]
        parents = {chunk.chunk_id for chunk in chunks if chunk.chunk_role == "parent"}
        block_ids = {block.block_id for block in normalized.blocks}
        if not children:
            raise ValueError("The document has no valid retrievable text; OCR is not performed")
        if len({chunk.chunk_id for chunk in chunks}) != len(chunks) or any(
            child.parent_chunk_id not in parents or not child.source_block_ids or
            not set(child.source_block_ids).issubset(block_ids) or not child.physical_page_numbers or
            any(number < 1 or number > len(normalized.pages) for number in child.physical_page_numbers)
            for child in children
        ):
            raise ValueError("Structured chunk validation failed: invalid parent/block/page provenance")
        represented = {block_id for child in children for block_id in child.source_block_ids}
        required = {block.block_id for block in normalized.blocks if block.display_text.strip()
                    and block.block_type not in {"page_header", "page_footer", "heading"}}
        if required - represented:
            raise ValueError(f"Tokenizer context excluded {len(required - represented)} text blocks; refusing an incomplete activation")
        metrics.parent_chunks, metrics.total_chunks = len(parents), len(children)
        item.index_status = "embedding"
        phase("embedding", 50, "Encoding contextual E5 children")
        vectors: dict[str, list[float]] = {}
        batch_size = embedder.settings.embedding_batch_size
        for offset in range(0, len(children), batch_size):
            batch = children[offset:offset + batch_size]
            # The encoder checks token length and every vector immediately before
            # and after inference. Already-prefixed contextual text is preserved.
            encoded = embedder.embed_batch([child.embedding_text for child in batch])
            if len(encoded) != len(batch):
                raise ValueError("Semantic vector count does not match the input batch")
            vectors.update((child.chunk_id, vector) for child, vector in zip(batch, encoded))
            metrics.processed_chunks += len(batch)
            job.progress = 50 + int(metrics.processed_chunks / len(children) * 40)
            job.detail = f"Embedded {metrics.processed_chunks}/{len(children)} semantic children"
            checkpoint()
        records = [ShreckWorldDocumentChunk(
            id=chunk.chunk_id, library_item_id=item.id, document_id=job.id,
            page_number=min(chunk.physical_page_numbers or [1]), ordinal=chunk.chunk_index,
            text=chunk.display_text, embedding_json=json.dumps(vectors.get(chunk.chunk_id, [])),
            chunk_role=chunk.chunk_role, parent_chunk_id=chunk.parent_chunk_id,
            section_id=chunk.parent_section_id, content_type=chunk.content_type,
            embedding_text=chunk.embedding_text, heading_path=chunk.heading_path,
            source_block_ids=chunk.source_block_ids, page_numbers=chunk.physical_page_numbers,
            token_count=_token_count(embedder.tokenizer, chunk.embedding_text) if chunk.embedding_eligible else None,
        ) for chunk in chunks]
        document = ShreckWorldDocument(id=job.id, library_item_id=item.id,
            source_sha256=item.source_sha256, parser_name=parsed.parser_name, parser_version=parsed.parser_version,
            embedding_version=EMBEDDING_VERSION, embedding_model=embedder.settings.embedding_model_id,
            embedding_fingerprint=metrics.embedding_fingerprint, embedding_dimension=embedder.settings.embedding_dimension,
            manifest_path=str((output_dir / "ingestion_manifest.json").resolve()))
        phase("storing", 95, "Staging vectors and activating structured SQL records")
        manifest = {"ingestion_id": job.id, "source_sha256": item.source_sha256, "status": "prepared",
                    "embedding_version": EMBEDDING_VERSION, "metrics": metrics.model_dump()}
        _atomic_json(Path(document.manifest_path), manifest)
        attempted_projection = True
        upsert_item_chunks(item, [record for record in records if record.chunk_role == "child"], document=document)
        previous = activate_document(session, item=item, document=document, normalized=normalized, chunks=records)
        metrics.phase_seconds["storing"] = round(monotonic() - phase_started, 3)
        metrics.phase, metrics.progress_mode = "completed", "determinate"
        now = datetime.now(timezone.utc)
        item.embedding_status, item.embedded_at, item.index_status = "ready", now, "ready"
        job.status, job.progress, job.completed_at, job.updated_at = "completed", 100, now, now
        job.detail = f"Embedded {len(children)} semantic children with {len(parents)} parents"
        job.metrics_json = metrics.model_dump_json()
        session.commit()
    except Exception as exc:
        session.rollback()
        metrics.phase_seconds[metrics.phase] = round(monotonic() - phase_started, 3)
        metrics.phase, metrics.progress_mode = "failed", "determinate"
        item = session.get(ShreckWorldLibraryItem, library_item_id)
        job = session.get(ShreckWorldEmbeddingJob, job_id)
        if item is None or job is None:
            return
        prior = session.scalar(select(ShreckWorldDocument.id).where(ShreckWorldDocument.library_item_id == item.id, ShreckWorldDocument.is_active.is_(True)))
        item.embedding_status, item.embedding_error = "ready" if prior else "failed", str(exc)[:2000]
        item.index_status, item.index_error = "failed", item.embedding_error
        now = datetime.now(timezone.utc)
        job.status, job.detail, job.completed_at, job.updated_at = "failed", item.embedding_error, now, now
        job.metrics_json = metrics.model_dump_json()
        session.commit()
        if attempted_projection:
            try:
                delete_document_chunks(job_id)
            except Exception:
                logger.warning("Unable to remove inactive Qdrant staging points for %s", job_id)
        try:
            _atomic_json(output_dir / "ingestion_manifest.json", {"ingestion_id": job_id, "status": "failed", "error": job.detail, "metrics": metrics.model_dump()})
        except OSError:
            logger.warning("Unable to write failed ingestion manifest for %s", job_id)
        return
    # Activation already committed. Cleanup/artifact errors cannot turn a valid
    # active generation into a failed job or deactivate its canonical records.
    try:
        for document_id in previous:
            delete_document_chunks(document_id)
        manifest["status"], manifest["metrics"] = "completed", metrics.model_dump()
        _atomic_json(Path(document.manifest_path), manifest)
    except Exception:
        logger.warning("Post-activation cleanup incomplete for %s; inactive generations remain excluded", job_id)
