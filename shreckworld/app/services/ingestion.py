"""Structured Docling/E5 ingestion with SQL activation and versioned Qdrant data."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from sqlalchemy import select
from sqlalchemy.orm import Session
from shreckdoc.pipeline import EMBEDDING_VERSION, TokenCountingTokenizer, _atomic_json, _sha256, _token_count, build_chunks
from shreckdoc.structure_content import project_resolved_document
from shreckdoc.structure_headings import infer_heading_candidates
from shreckdoc.structure_resolution import resolve_structure
from shreckdoc.structure_toc import extract_toc_candidates, map_printed_pages
from shreckdoc.table_text import serialize_table_text

from app.models import ShreckWorld, ShreckWorldDocument, ShreckWorldDocumentChunk, ShreckWorldEmbeddingJob, ShreckWorldLibraryItem
from app.core.config import get_settings
from app.repositories.documents import activate_document, BOOK_MAP_EMBEDDING_VERSION
from app.schemas.shreckworld import EmbeddingJobRead, IndexingJobMetrics
from app.services.document_parsing import parse_source, with_heartbeat
from app.services.embedding import get_embedder
from app.services.retrieval_index import upsert_item_chunks, delete_document_chunks
from app.services.pdf_structure_evidence import collect_pdf_structure
from app.services.book_map_v4 import build_book_map_v4

logger = logging.getLogger(__name__)


def _log_indexing_summary(job: ShreckWorldEmbeddingJob, metrics: IndexingJobMetrics) -> None:
    """Log persisted terminal timings without source text, vectors, or secrets."""
    result = EmbeddingJobRead.model_validate(job)
    summary = {
        "job_id": job.id, "library_item_id": job.library_item_id, "status": job.status,
        "embedding_backend": metrics.embedding_backend,
        "parser_name": metrics.parser_name, "parser_version": metrics.parser_version,
        "parser_device": metrics.parser_device, "parser_table_former_mode": metrics.parser_table_former_mode,
        "embedding_device": metrics.embedding_device,
        "elapsed_seconds": result.elapsed_seconds, "queue_wait_seconds": result.queue_wait_seconds,
        "processing_seconds": result.processing_seconds,
        "phase_seconds": metrics.phase_seconds, "parser_seconds": metrics.parser_seconds,
        "parser_cache_hit": metrics.parser_cache_hit,
        "parser_table_structure_enabled": metrics.parser_table_structure_enabled,
        "processed_chunks": metrics.processed_chunks, "total_chunks": metrics.total_chunks,
    }
    logger.log(logging.INFO if job.status == "completed" else logging.WARNING,
               "[SHRECKWORLD_INDEXING_SUMMARY] %s", json.dumps(summary, sort_keys=True))


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
        settings = get_settings()
        metrics.embedding_backend = embedder.backend
        metrics.embedding_model = embedder.settings.embedding_model_id
        metrics.embedding_device = settings.embedding_device
        metrics.embedding_fingerprint = str(health["fingerprint"])
        metrics.embedding_revision = str(health["revision"])
        metrics.tokenizer_limit = embedder.token_limit
        path = Path(item.storage_path or item.pdf_path)
        if _sha256(path) != item.source_sha256:
            raise ValueError("Source hash changed after upload; refusing to index a different document")
        world = session.get(ShreckWorld, item.shreckworld_id) if item.shreckworld_id else None
        parser = settings.pdf_parser if path.suffix.lower() == ".pdf" else ("plain text" if path.suffix.lower() == ".txt" else "docling")
        metrics.parser_name = parser.replace(" ", "_")
        metrics.parser_device = settings.parser_device if parser == "docling" else None
        metrics.parser_table_former_mode = settings.parser_table_former_mode if parser == "docling" else None
        metrics.parser_table_structure_enabled = (settings.parser_table_structure_enabled if parser == "docling" or not settings.parser_table_structure_enabled else None)
        parse_detail = f"Parsing structure with MinerU ({settings.mineru_tier})" if parser == "mineru" else f"Parsing structure with {parser}; OCR disabled"
        phase("parsing", 5, parse_detail, indeterminate=True)

        def parsing_heartbeat(total: int | None) -> None:
            metrics.total_pages = total
            checkpoint(force=True)

        def parsing_pages(done: int, total: int) -> None:
            metrics.processed_pages = max(metrics.processed_pages, min(done, total))
            metrics.total_pages = total
            metrics.progress_mode = "determinate"
            job.progress = max(job.progress, 5 + int(metrics.processed_pages / total * 29))
            ocr_note = "; OCR disabled" if parser != "mineru" else "; MinerU OCR may be used"
            job.detail = f"Processed {metrics.processed_pages}/{total} PDF pages; completing structured parsing{ocr_note}"
            checkpoint(force=True)

        parsed = parse_source(path, ingestion_id=job.id, output_dir=output_dir, heartbeat=parsing_heartbeat,
                              page_progress=parsing_pages, force_reparse=True)
        normalized = parsed.document
        resolver_started = monotonic()
        pdf_evidence = collect_pdf_structure(path) if path.suffix.lower() == ".pdf" else None
        toc_extraction = extract_toc_candidates(normalized)
        preliminary_headings = infer_heading_candidates(normalized)
        toc_mapping = map_printed_pages(toc_extraction.candidates,
            [candidate for candidate in preliminary_headings.candidates if candidate.disposition == "candidate"])
        toc_rows = toc_mapping.candidates
        heading_result = infer_heading_candidates(normalized,
            toc_titles={row.title for row in toc_rows},
            toc_source_block_ids={row.source_block_id for row in toc_rows})
        resolution = resolve_structure(document_id=job.id, document=normalized,
            bookmarks=pdf_evidence.outline if pdf_evidence else (), toc_candidates=toc_rows,
            heading_result=heading_result)
        strategy = []
        if pdf_evidence and pdf_evidence.outline:
            strategy.append("pdf_bookmarks")
        if toc_rows:
            strategy.append("printed_toc")
        if heading_result.candidates:
            strategy.append("inferred_headings")
        metrics.extraction_strategy = strategy or ["content_order"]
        resolved_document = project_resolved_document(normalized, resolution)
        metrics.resolver_seconds = round(monotonic() - resolver_started, 3)
        metrics.resolver_conflicts = len(resolution.conflicts)
        metrics.parser_name, metrics.parser_version = parsed.parser_name, parsed.parser_version
        metrics.parser_mineru_tier = settings.mineru_tier if parsed.parser_name == "mineru" else None
        metrics.parse_quality = parsed.quality
        metrics.parser_cache_hit = parsed.cache_hit
        metrics.parser_seconds = parsed.phase_seconds
        metrics.warnings.extend(parsed.diagnostics)
        metrics.processed_pages = metrics.total_pages = len(normalized.pages)
        text_pages = {number for block in normalized.blocks if block.display_text.strip() and block.block_type != "heading" for number in block.page_numbers}
        metrics.text_pages, metrics.empty_pages = len(text_pages), len(normalized.pages) - len(text_pages)
        metrics.extracted_characters = sum(len(block.display_text) for block in normalized.blocks)
        metrics.sections, metrics.blocks = len(resolved_document.sections), len(resolved_document.blocks)
        if metrics.empty_pages:
            note = "OCR was attempted by MinerU where required." if parsed.parser_name == "mineru" else "OCR is not performed."
            metrics.warnings.append(f"{metrics.empty_pages} pages yielded no retrievable text; {note}")
        scope_id, book_title, system_name = str(item.ontology_id) if item.ontology_id is not None else item.shreckworld_id, item.title, world.name if world and item.ontology_id is None else ""
        ingestion_id, source_item_id = job.id, item.id
        phase("chunking", 35, "Building structural parents and tokenizer-bounded E5 children", indeterminate=True)
        tokenizer = TokenCountingTokenizer(embedder.tokenizer)
        serialized_tables = {block.block_id: serialize_table_text(block.table_structure, fallback_text=block.display_text)
            for block in resolved_document.blocks if block.block_type == "table"}
        metrics.table_text_fallbacks = sum(value.quality != "structured" for value in serialized_tables.values())
        chunks = with_heartbeat(lambda: build_chunks(
            document=resolved_document, ingestion_id=ingestion_id, library_item_id=source_item_id,
            ontology_id=scope_id, book_title=book_title, rpg_system=system_name,
            tokenizer=tokenizer, max_embedding_tokens=embedder.token_limit,
            preserve_unit_boundaries=parsed.parser_name == "pymupdf" or any(
                block.block_type in {"table", "picture"} for block in resolved_document.blocks
            ),
            table_text_by_block={block_id: value.text if value.quality == "structured" else ""
                for block_id, value in serialized_tables.items()},
            table_row_groups_by_block={block_id: value.row_groups for block_id, value in serialized_tables.items()
                if value.row_groups},
        ), lambda: checkpoint(force=True))
        children = [chunk for chunk in chunks if chunk.chunk_role == "child"]
        parents = {chunk.chunk_id for chunk in chunks if chunk.chunk_role == "parent"}
        block_ids = {block.block_id for block in normalized.blocks}
        if not children:
            detail = "MinerU returned no valid retrievable text" if parsed.parser_name == "mineru" else "The document has no valid retrievable text; OCR is not performed"
            raise ValueError(detail)
        if len({chunk.chunk_id for chunk in chunks}) != len(chunks) or any(
            child.parent_chunk_id not in parents or not child.source_block_ids or
            not set(child.source_block_ids).issubset(block_ids) or not child.physical_page_numbers or
            any(number < 1 or number > len(normalized.pages) for number in child.physical_page_numbers)
            for child in children
        ):
            raise ValueError("Structured chunk validation failed: invalid parent/block/page provenance")
        represented = {block_id for child in children for block_id in child.source_block_ids}
        # Every extracted textual block must be represented. Heading text is
        # included through each chunk's heading_path; images remain references
        # with caption text indexed as ordinary evidence blocks.
        required = {block.block_id for block in normalized.blocks if block.display_text.strip()
                    and block.block_type not in {"heading", "picture", "image", "unparsed_region"}
                    and (block.block_type != "table" or
                         serialized_tables[block.block_id].quality == "structured")}
        if required - represented:
            raise ValueError(f"Tokenizer context excluded {len(required - represented)} text blocks; refusing an incomplete activation")
        token_counts = {child.chunk_id: _token_count(tokenizer, child.embedding_text) for child in children}
        chunk_ids_by_block: dict[str, list[str]] = {}
        for child in children:
            for block_id in child.source_block_ids:
                chunk_ids_by_block.setdefault(block_id, []).append(child.chunk_id)
        v4_map = build_book_map_v4(document_id=job.id, source_sha256=item.source_sha256,
            document=normalized, resolution=resolution, parser_name=parsed.parser_name,
            parser_version=parsed.parser_version, chunk_ids_by_block=chunk_ids_by_block, index_alignment="aligned")
        metrics.book_map_version = 4
        metrics.resolved_nodes = len(v4_map.nodes)
        metrics.uncertain_nodes = len(v4_map.quality.uncertain_node_ids)
        metrics.unresolved_nodes = len(v4_map.quality.unresolved_node_ids)
        metrics.extracted_pages = v4_map.quality.extracted_page_count
        metrics.unknown_pages = v4_map.quality.unknown_page_count
        metrics.context_reduced_chunks = sum(bool(getattr(chunk, "context_reduced", False)) for chunk in children)
        metrics.table_nodes = v4_map.quality.table_node_count
        metrics.image_nodes = v4_map.quality.image_node_count
        metrics.textless_image_nodes = sum(block.block_type in {"picture", "image"} and not block.display_text.strip()
                                           for block in normalized.blocks)
        metrics.intentional_image_pixel_exclusions = metrics.image_nodes
        metrics.embedded_source_blocks = len(represented)
        metrics.parent_chunks, metrics.total_chunks = len(parents), len(children)
        item.index_status = "embedding"
        phase("embedding", 50, "Encoding contextual E5 children")
        vectors: dict[str, list[float]] = {}
        batch_size = embedder.settings.embedding_batch_size
        metrics.embedding_batch_size = batch_size
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
            token_count=token_counts.get(chunk.chunk_id), context_reduced=chunk.context_reduced,
        ) for chunk in chunks]
        embedding_version = BOOK_MAP_EMBEDDING_VERSION
        document = ShreckWorldDocument(id=job.id, library_item_id=item.id,
            source_sha256=item.source_sha256, parser_name=parsed.parser_name, parser_version=parsed.parser_version,
            embedding_version=embedding_version, embedding_model=embedder.settings.embedding_model_id,
            embedding_fingerprint=metrics.embedding_fingerprint, embedding_dimension=embedder.settings.embedding_dimension,
            manifest_path=str((output_dir / "ingestion_manifest.json").resolve()))
        phase("storing", 95, "Staging vectors and activating structured SQL records")
        manifest = {"ingestion_id": job.id, "source_sha256": item.source_sha256, "status": "prepared",
                    "embedding_version": embedding_version, "book_map_version": 4,
                    "resolver_version": v4_map.resolver_version, "chunking_version": v4_map.chunking_version,
                    "index_alignment": v4_map.index_alignment, "book_map_quality": v4_map.quality.model_dump(),
                    "metrics": metrics.model_dump()}
        _atomic_json(Path(document.manifest_path), manifest)
        _atomic_json(output_dir / "book_map_v4.json", v4_map.model_dump(mode="json"))
        _atomic_json(output_dir / "book_map_evidence.json", {
            "resolver_version": v4_map.resolver_version,
            "resolution": {"nodes": [asdict(node) for node in resolution.nodes],
                           "diagnostics": resolution.diagnostics, "conflicts": resolution.conflicts},
            "pdf_structure_diagnostics": pdf_evidence.diagnostics if pdf_evidence else [],
            "toc_diagnostics": [*toc_extraction.diagnostics, *toc_mapping.diagnostics],
        })
        attempted_projection = True
        upsert_item_chunks(item, [record for record in records if record.chunk_role == "child"], document=document)
        previous = activate_document(session, item=item, document=document, normalized=resolved_document,
                                     chunks=records, book_map_payload=v4_map.model_dump(mode="json"))
        metrics.phase_seconds["storing"] = round(monotonic() - phase_started, 3)
        metrics.phase, metrics.progress_mode = "completed", "determinate"
        now = datetime.now(timezone.utc)
        item.embedding_status, item.embedded_at, item.index_status = "ready", now, "ready"
        item.indexing_provenance = {
            "strategy": metrics.extraction_strategy,
            "book_map_version": metrics.book_map_version,
            "parser_name": metrics.parser_name, "parser_version": metrics.parser_version,
            "parser_device": metrics.parser_device, "parser_mineru_tier": metrics.parser_mineru_tier,
            "parser_table_structure_enabled": metrics.parser_table_structure_enabled,
            "parser_table_former_mode": metrics.parser_table_former_mode,
            "embedding_backend": metrics.embedding_backend, "embedding_model": metrics.embedding_model,
            "embedding_device": metrics.embedding_device, "embedding_version": embedding_version,
        }
        job.status, job.progress, job.completed_at, job.updated_at = "completed", 100, now, now
        job.detail = f"Embedded {len(children)} semantic children with {len(parents)} parents"
        job.metrics_json = metrics.model_dump_json()
        session.commit()
        _log_indexing_summary(job, metrics)
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
        _log_indexing_summary(job, metrics)
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
