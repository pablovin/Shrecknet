"""ShreckWorld source adapter and progress heartbeats around shared algorithms."""
from __future__ import annotations

import re
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import asdict, dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Callable, TypeVar

from shreckdoc.pipeline import (
    DoclingAdapter, NormalizedDocument, _atomic_json, _native_pdf_page_count,
    _parse_quality_summary, normalize_document,
)

from app.core.config import get_settings

T = TypeVar("T")


class ParserDiagnostics(logging.Handler):
    """Retain explicit parser warnings, including TableFormer cell-loss reports."""
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []
        self.records = self.drop_reports = self.dropped_cells = self.reported_cells = 0

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        loss = re.search(r"(\d+) of (\d+) pdf cells .* were dropped from the table", message)
        if not loss and not record.name.startswith(("docling", "docling_core", "docling_ibm_models")):
            return
        self.records += 1
        if loss:
            self.drop_reports += 1
            self.dropped_cells += int(loss[1])
            self.reported_cells += int(loss[2])
        bounded = f"{record.levelname.lower()}: {message[:1000]}"
        if bounded not in self.messages and len(self.messages) < 32:
            self.messages.append(bounded)


def with_heartbeat(work: Callable[[], T], heartbeat: Callable[[], None]) -> T:
    """Keep SQL progress observable while opaque local parsing/chunking runs."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(work)
        while True:
            try:
                return future.result(timeout=get_settings().parser_heartbeat_seconds)
            except TimeoutError:
                if future.done():
                    return future.result()
                heartbeat()


@dataclass
class ParsedSource:
    document: NormalizedDocument
    parser_name: str
    parser_version: str
    diagnostics: list[str]
    quality: dict


def parse_source(path: Path, *, ingestion_id: str, output_dir: Path,
                 heartbeat: Callable[[int | None], None]) -> ParsedSource:
    settings = get_settings()
    output_dir.mkdir(parents=True, exist_ok=True)
    native_count = _native_pdf_page_count(path) if path.suffix.lower() == ".pdf" else None
    heartbeat(native_count)
    capture = ParserDiagnostics()
    if path.suffix.lower() == ".txt":
        content = path.read_text(encoding="utf-8", errors="replace")
        canonical = {"pages": {"1": {}}}
        items = [{"reference": f"text-{index}", "block_type": "paragraph", "text": part,
                  "reading_order": index, "provenance": [{"page_no": 1}]}
                 for index, part in enumerate(re.split(r"\n\s*\n", content)) if part.strip()]
        diagnostics = []
        parser_name, parser_version = "plain_text", "1"
        _atomic_json(output_dir / "source_document.json", {"pages": canonical["pages"], "items": items})
    else:
        adapter = DoclingAdapter(
            str(settings.docling_artifacts_path) if settings.docling_artifacts_path else None,
            device=settings.embedding_device, log_prefix="[SHRECKWORLD_INDEX]", require_complete=True,
        )
        root_logger = logging.getLogger()
        root_logger.addHandler(capture)
        try:
            _, canonical, _, items, diagnostics = with_heartbeat(
                lambda: adapter.parse(path, output_dir), lambda: heartbeat(native_count)
            )
        finally:
            root_logger.removeHandler(capture)
        diagnostics = list(dict.fromkeys([*diagnostics, *capture.messages]))
        parser_name, parser_version = "docling", version("docling")
        if path.suffix.lower() != ".pdf":
            # DOCX/Markdown have logical page 1, rather than PDF coordinates.
            canonical["pages"] = {"1": {}}
            for item in items:
                item["provenance"] = [{"page_no": 1}]
    document = normalize_document(
        ingestion_id=ingestion_id, pdf_path=path, canonical=canonical, items=items,
        page_labeler=None if path.suffix.lower() == ".pdf" else lambda _, count: {number: str(number) for number in range(1, count + 1)},
    )
    if native_count is not None and len(document.pages) != native_count:
        raise ValueError("Docling page count does not match the source PDF; refusing a partial activation")
    if not document.pages:
        raise ValueError("The parser returned no document pages")
    quality = _parse_quality_summary(native_page_count=native_count, canonical=canonical, items=items, diagnostics=diagnostics)
    quality.update(parser_log_records=capture.records, table_cell_drop_reports=capture.drop_reports,
                   reported_dropped_table_cells=capture.dropped_cells, reported_table_cells=capture.reported_cells)
    _atomic_json(output_dir / "normalized_document.json", asdict(document))
    return ParsedSource(document, parser_name, parser_version, diagnostics, quality)
