"""Shared Docling adapter, normalized document records, and E5 chunk builder.

Extracted from Librarian: no app, SQL, Neo4j, Celery, or model runtime imports.
The pipeline parses -> normalizes pages/sections/blocks -> builds parents and
contextual, tokenizer-bounded children. Callers own storage and inference.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import NAMESPACE_URL, uuid5

logger = logging.getLogger(__name__)
PARSER_NAME = "docling"
EMBEDDING_VERSION = "docling-e5-context-v1"
TARGET_EMBEDDING_TOKENS = 400
MAX_EMBEDDING_TOKENS = 500

@dataclass(slots=True)
class NormalizedPage:
    page_id: str
    physical_page_number: int
    displayed_page_label: str | None
    width: float | None = None
    height: float | None = None


@dataclass(slots=True)
class NormalizedSection:
    section_id: str
    parent_section_id: str | None
    heading: str | None
    heading_level: int
    heading_path: list[str] = field(default_factory=list)
    block_ids: list[str] = field(default_factory=list)
    page_numbers: list[int] = field(default_factory=list)
    page_labels: list[str] = field(default_factory=list)


@dataclass(slots=True)
class NormalizedBlock:
    block_id: str
    section_id: str
    block_type: str
    display_text: str
    page_numbers: list[int] = field(default_factory=list)
    page_labels: list[str] = field(default_factory=list)
    bounding_boxes: list[dict[str, Any]] = field(default_factory=list)
    reading_order: int = 0
    docling_reference: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedChunk:
    chunk_id: str
    ingestion_id: str
    library_item_id: int | str
    ontology_id: int | str
    chunk_role: str
    content_type: str
    display_text: str
    embedding_text: str
    book_title: str
    rpg_system: str
    heading_path: list[str]
    primary_heading: str | None
    block_types: list[str]
    physical_page_numbers: list[int]
    displayed_page_labels: list[str]
    bounding_boxes: list[dict[str, Any]]
    parent_chunk_id: str | None
    parent_section_id: str
    source_block_ids: list[str]
    chunk_index: int
    embedding_eligible: bool
    fulltext_eligible: bool = True
    text_embedding: list[float] | None = None

    @property
    def heading_path_text(self) -> str:
        return " > ".join(self.heading_path)


@dataclass(slots=True)
class NormalizedDocument:
    pages: list[NormalizedPage]
    sections: list[NormalizedSection]
    blocks: list[NormalizedBlock]


def _stable_id(kind: str, ingestion_id: str, value: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"shrecknet:{kind}:{ingestion_id}:{value}"))


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temp.replace(path)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(value, encoding="utf-8")
    temp.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def _native_pdf_page_count(path: Path) -> int | None:
    """Read page count only; native text remains exclusively Docling's source."""
    try:
        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader
        return len(PdfReader(str(path)).pages)
    except Exception as exc:
        logger.warning("Unable to count PDF pages before Docling parse path=%s error=%s", path, exc)
        return None


def _cuda_heartbeat() -> dict[str, int | str]:
    """Lightweight evidence that a CUDA-backed parser is still active."""
    try:
        import torch

        if torch.cuda.is_available():
            return {
                "device": torch.cuda.get_device_name(0),
                "allocated_mb": round(torch.cuda.memory_allocated(0) / 1024 / 1024),
                "reserved_mb": round(torch.cuda.memory_reserved(0) / 1024 / 1024),
            }
    except Exception:
        pass
    return {"device": "cpu"}


def _primitive(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_primitive(item) for item in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return _primitive(dump(mode="json"))
    return str(value)


def _label_name(item: Any) -> str:
    label = getattr(item, "label", None)
    return str(getattr(label, "value", label) or "unknown").lower()


def _block_type(label: str) -> str:
    normalized = label.lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "section_header": "heading",
        "title": "heading",
        "text": "paragraph",
        "list_item": "list_item",
        "ordered_list": "list",
        "unordered_list": "list",
        "picture": "picture",
        "table": "table",
        "caption": "caption",
        "formula": "formula",
        "code": "code",
        "key_value_region": "key_value",
        "page_header": "page_header",
        "page_footer": "page_footer",
    }
    return aliases.get(normalized, normalized if normalized in set(aliases.values()) else "unknown")


def _bbox_dict(prov: Any) -> dict[str, Any]:
    bbox = getattr(prov, "bbox", None)
    if bbox is None:
        return {}
    return {
        "left": getattr(bbox, "l", None),
        "top": getattr(bbox, "t", None),
        "right": getattr(bbox, "r", None),
        "bottom": getattr(bbox, "b", None),
        "coord_origin": str(getattr(getattr(bbox, "coord_origin", None), "value", "")) or None,
    }


def _page_label_map(pdf_path: Path, page_count: int) -> dict[int, str | None]:
    """Read displayed labels without using PDF page text as an ingestion source."""
    labels: dict[int, str | None] = {
        number: str(number) for number in range(1, page_count + 1)
    }
    try:
        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader

        reader = PdfReader(str(pdf_path))
        page_labels = getattr(reader, "page_labels", None)
        if page_labels:
            for index, label in enumerate(page_labels, start=1):
                if index <= page_count and label is not None:
                    labels[index] = str(label)
    except Exception as exc:
        logger.warning("docling_page_labels_unavailable path=%s error=%s", pdf_path, exc)
    return labels


class DoclingAdapter:
    """Own all interaction with Docling and return primitive/local models."""

    def __init__(self, artifacts_path: str | None = None, *, device: str | None = None, log_prefix: str = "[LIBRARIAN_EMBED]", require_complete: bool = False) -> None:
        self.artifacts_path = artifacts_path or os.getenv("DOCLING_ARTIFACTS_PATH")
        self.device = device
        self.log_prefix = log_prefix
        self.require_complete = require_complete

    def parse(self, pdf_path: Path, output_dir: Path) -> tuple[Any, dict[str, Any], str, list[dict[str, Any]], list[str]]:
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import (
                PdfPipelineOptions,
                TableFormerMode,
            )
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling_core.types.doc import ImageRefMode
        except ImportError as exc:
            raise RuntimeError("Docling is required for structured document ingestion") from exc

        accelerator = {}
        if self.device is not None:
            from docling.datamodel.accelerator_options import AcceleratorOptions
            accelerator["accelerator_options"] = AcceleratorOptions(device=self.device)
        options = PdfPipelineOptions(
            **accelerator,
            artifacts_path=self.artifacts_path,
            enable_remote_services=False,
            allow_external_plugins=False,
            # Library PDFs are ingested only from their native text layer. OCR
            # can alter source wording and is intentionally disabled, including
            # selective/page-level OCR fallback.
            do_ocr=False,
            do_table_structure=True,
            generate_parsed_pages=True,
            # Picture/page assets are not used by retrieval or synthesis. Keep
            # structural picture blocks and provenance, but do not spend disk
            # space exporting thousands of PNGs per book.
            generate_page_images=False,
            generate_picture_images=False,
        )
        if hasattr(options.table_structure_options, "mode"):
            options.table_structure_options.mode = TableFormerMode.ACCURATE
        hierarchy = getattr(options, "heading_hierarchy_options", None)
        if hierarchy is not None:
            hierarchy.enabled = True
            if hasattr(hierarchy, "use_bookmarks"):
                hierarchy.use_bookmarks = True
            if hasattr(hierarchy, "use_style"):
                hierarchy.use_style = True

        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        )
        export_started = time.monotonic()
        result = converter.convert(str(pdf_path))
        status = str(getattr(getattr(result, "status", None), "value", getattr(result, "status", "success")))
        if self.require_complete and status != "success":
            raise RuntimeError(f"Docling conversion did not succeed: {status}")
        document = result.document
        print(
            f"{self.log_prefix} step=docling_convert_complete file={pdf_path.name} "
            f"next=export_canonical_artifacts elapsed_s={time.monotonic() - export_started:.2f}"
        )
        json_path = output_dir / "docling_document.json"
        json_temp = output_dir / "docling_document.json.tmp"
        document.save_as_json(
            json_temp,
            image_mode=ImageRefMode.PLACEHOLDER,
            indent=2,
        )
        json_temp.replace(json_path)
        print(
            f"{self.log_prefix} step=canonical_json_written file={pdf_path.name} "
            f"elapsed_s={time.monotonic() - export_started:.2f}"
        )
        canonical = json.loads(json_path.read_text(encoding="utf-8"))
        markdown_path = output_dir / "document.md"
        markdown_temp = output_dir / "document.md.tmp"
        document.save_as_markdown(
            markdown_temp,
            image_mode=ImageRefMode.PLACEHOLDER,
        )
        markdown_temp.replace(markdown_path)
        markdown = markdown_path.read_text(encoding="utf-8")
        print(
            f"{self.log_prefix} step=debug_markdown_written file={pdf_path.name} "
            f"next=normalize_structured_blocks elapsed_s={time.monotonic() - export_started:.2f}"
        )
        primitives = self._items_to_primitives(document)
        diagnostics = self._conversion_diagnostics(result)
        print(
            f"{self.log_prefix} step=docling_artifact_export_complete file={pdf_path.name} "
            f"items={len(primitives)} elapsed_s={time.monotonic() - export_started:.2f}"
        )
        return document, canonical, markdown, primitives, diagnostics

    @staticmethod
    def _conversion_diagnostics(result: Any) -> list[str]:
        """Collect only explicit parser diagnostics; never guess a failed table."""
        diagnostics: list[str] = []
        for attribute in ("errors", "warnings"):
            values = getattr(result, attribute, None) or []
            if isinstance(values, (str, bytes)):
                values = [values]
            for value in values:
                message = str(value).strip()
                if message:
                    diagnostics.append(f"{attribute[:-1]}: {message}")
        return diagnostics

    def _items_to_primitives(self, document: Any) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        try:
            iterator: Iterable[Any] = document.iterate_items(with_groups=True)
        except TypeError:
            iterator = document.iterate_items()

        for order, entry in enumerate(iterator):
            item, level = entry if isinstance(entry, tuple) else (entry, 0)
            label = _label_name(item)
            kind = _block_type(label)
            text = str(getattr(item, "text", "") or "").strip()
            if kind == "table":
                exporter = getattr(item, "export_to_markdown", None)
                if callable(exporter):
                    try:
                        text = exporter(doc=document).strip()
                    except TypeError:
                        text = exporter(document).strip()
            reference = str(getattr(item, "self_ref", "") or f"item-{order}")
            parent = getattr(item, "parent", None)
            parent_ref = str(getattr(parent, "cref", parent) or "") or None
            provs = list(getattr(item, "prov", None) or [])
            provenance = [
                {
                    "page_no": int(getattr(prov, "page_no", 1)),
                    "bbox": _bbox_dict(prov),
                    "charspan": _primitive(getattr(prov, "charspan", None)),
                }
                for prov in provs
            ]
            records.append(
                {
                    "reference": reference,
                    "parent_reference": parent_ref,
                    "label": label,
                    "block_type": kind,
                    "text": text,
                    "level": int(getattr(item, "level", level) or level or 0),
                    "reading_order": order,
                    "provenance": provenance,
                    "image_path": None,
                }
            )
        return records


def normalize_document(
    *, ingestion_id: str, pdf_path: Path, canonical: dict[str, Any], items: list[dict[str, Any]],
    page_labeler: Callable[[Path, int], dict[int, str | None]] | None = None,
) -> NormalizedDocument:
    raw_pages = canonical.get("pages") or {}
    if isinstance(raw_pages, list):
        page_entries = list(enumerate(raw_pages, start=1))
    else:
        page_entries = sorted(raw_pages.items(), key=lambda pair: int(pair[0]))
    page_count = max(
        len(page_entries),
        max((int(prov.get("page_no") or 1) for item in items for prov in item.get("provenance", [])), default=0),
    )
    labels = (page_labeler or _page_label_map)(pdf_path, page_count)
    pages: list[NormalizedPage] = []
    for number in range(1, page_count + 1):
        raw = page_entries[number - 1][1] if number <= len(page_entries) else {}
        size = raw.get("size", {}) if isinstance(raw, dict) else {}
        pages.append(
            NormalizedPage(
                page_id=_stable_id("page", ingestion_id, str(number)),
                physical_page_number=number,
                displayed_page_label=labels.get(number),
                width=size.get("width"),
                height=size.get("height"),
            )
        )

    sections: list[NormalizedSection] = []
    blocks: list[NormalizedBlock] = []
    root_id = _stable_id("section", ingestion_id, "root")
    sections.append(NormalizedSection(root_id, None, None, 0, [], [], [], []))
    stack: list[NormalizedSection] = [sections[0]]
    page_by_number = {page.physical_page_number: page for page in pages}

    for item in sorted(items, key=lambda row: int(row.get("reading_order", 0))):
        kind = item.get("block_type") or "unknown"
        text = str(item.get("text") or "").strip()
        if kind == "heading" and text:
            level = max(1, int(item.get("level") or 1))
            while len(stack) > 1 and stack[-1].heading_level >= level:
                stack.pop()
            parent = stack[-1]
            section = NormalizedSection(
                section_id=_stable_id("section", ingestion_id, str(item.get("reference"))),
                parent_section_id=parent.section_id,
                heading=text,
                heading_level=level,
                heading_path=[*parent.heading_path, text],
            )
            sections.append(section)
            stack.append(section)

        section = stack[-1]
        provenance = item.get("provenance") or []
        physical_pages = sorted({int(prov.get("page_no") or 1) for prov in provenance})
        page_labels = [
            page_by_number[number].displayed_page_label
            for number in physical_pages
            if number in page_by_number and page_by_number[number].displayed_page_label is not None
        ]
        block = NormalizedBlock(
            block_id=_stable_id("block", ingestion_id, str(item.get("reference"))),
            section_id=section.section_id,
            block_type=kind,
            display_text=text,
            page_numbers=physical_pages,
            page_labels=[str(label) for label in page_labels],
            bounding_boxes=[prov.get("bbox") or {} for prov in provenance if prov.get("bbox")],
            reading_order=int(item.get("reading_order") or 0),
            docling_reference=str(item.get("reference") or "") or None,
            metadata={
                "label": item.get("label"),
                "parent_reference": item.get("parent_reference"),
                "image_path": item.get("image_path"),
                "provenance": provenance,
            },
        )
        blocks.append(block)
        section.block_ids.append(block.block_id)
        section.page_numbers = sorted(set(section.page_numbers + physical_pages))
        section.page_labels = list(dict.fromkeys(section.page_labels + block.page_labels))

    return NormalizedDocument(pages=pages, sections=sections, blocks=blocks)


def _content_type(blocks: list[NormalizedBlock], section: NormalizedSection) -> str:
    types = {block.block_type for block in blocks}
    if "table" in types:
        return "table"
    if "list" in types or "list_item" in types:
        return "list"
    if "picture" in types:
        return "picture"
    if "formula" in types:
        return "formula"
    if "code" in types:
        return "code"
    if "key_value" in types:
        return "key_value"
    return "section" if section.heading else "section_preamble"


def _normalize_embedding_notation(text: str) -> str:
    translations = {"×": " multiplied by ", "÷": " divided by ", "≤": " less than or equal to ", "≥": " greater than or equal to ", "−": "-"}
    for source, replacement in translations.items():
        text = text.replace(source, replacement)
    return re.sub(r"[ \t]+", " ", text).strip()


def build_embedding_text(chunk: NormalizedChunk) -> str:
    parts = [
        f"passage: Book: {chunk.book_title}",
        f"RPG System: {chunk.rpg_system or 'Unknown'}",
    ]
    if chunk.heading_path:
        parts.append(f"Section: {chunk.heading_path_text}")
    if chunk.primary_heading:
        parts.append(f"Entry: {chunk.primary_heading}")
    parts.append(f"Content Type: {chunk.content_type.replace('_', ' ').title()}")
    parts.append("")
    parts.append(_normalize_embedding_notation(chunk.display_text))
    return "\n".join(parts).strip()


def _token_count(tokenizer: Any, text: str) -> int:
    try:
        encoded = tokenizer(
            text,
            add_special_tokens=True,
            truncation=False,
            verbose=False,
        )
        return len(encoded["input_ids"])
    except (TypeError, KeyError):
        return len(tokenizer.encode(text, add_special_tokens=True))


def _token_window_split(
    tokenizer: Any,
    prototype: NormalizedChunk,
    text: str,
    *,
    max_tokens: int,
    overlap_tokens: int = 32,
) -> list[str]:
    """Last-resort split for one oversized atomic unit; never rely on model truncation."""
    header = NormalizedChunk(**{**asdict(prototype), "display_text": "", "embedding_text": ""})
    content_budget = max_tokens - _token_count(tokenizer, build_embedding_text(header))
    if content_budget < 8:
        # An extreme heading path can exhaust the model budget. Keep the book
        # ingestible by omitting this child, while retaining its complete parent.
        logger.warning(
            "embedding_child_skipped reason=context_header_exceeds_budget section=%s budget=%s",
            prototype.heading_path_text,
            max_tokens,
        )
        return []

    token_ids = tokenizer.encode(text, add_special_tokens=False)
    decode = getattr(tokenizer, "decode", None)
    if not callable(decode):
        # Test/dummy tokenizers do not necessarily provide decode. This remains
        # deterministic and is only used when no real tokenizer is available.
        words = text.split()
        return [" ".join(words[start : start + content_budget]) for start in range(0, len(words), max(1, content_budget - overlap_tokens))]

    pieces: list[str] = []
    start = 0
    stride = max(1, content_budget - min(overlap_tokens, content_budget - 1))
    while start < len(token_ids):
        end = min(len(token_ids), start + content_budget)
        piece = decode(
            token_ids[start:end],
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        ).strip()
        # Decoding can add tokenization whitespace; shrink until the final,
        # contextual E5 input is truly within budget.
        while end > start and (
            not piece
            or _token_count(
                tokenizer,
                build_embedding_text(
                    NormalizedChunk(**{**asdict(prototype), "display_text": piece, "embedding_text": ""})
                ),
            ) > max_tokens
        ):
            end -= 1
            piece = decode(
                token_ids[start:end],
                skip_special_tokens=True,
                clean_up_tokenization_spaces=False,
            ).strip()
        if not piece:
            logger.warning("embedding_child_skipped reason=token_window_empty section=%s", prototype.heading_path_text)
            break
        pieces.append(piece)
        if end >= len(token_ids):
            break
        start = max(start + 1, end - min(overlap_tokens, max(0, end - start - 1)))
    if pieces:
        logger.warning(
            "embedding_child_token_window_fallback section=%s windows=%s max_tokens=%s overlap_tokens=%s",
            prototype.heading_path_text,
            len(pieces),
            max_tokens,
            overlap_tokens,
        )
    return pieces


def _model_token_limit(model: Any) -> int:
    """Use the model's real tokenizer/model limit, never a guessed truncation budget."""
    candidates = [MAX_EMBEDDING_TOKENS]
    for value in (
        getattr(model, "max_seq_length", None),
        getattr(getattr(model, "tokenizer", None), "model_max_length", None),
    ):
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        # Hugging Face uses a huge sentinel for unknown limits.
        if 0 < parsed < 100_000:
            candidates.append(parsed)
    return min(candidates)


def _split_for_embedding(
    tokenizer: Any, prototype: NormalizedChunk, text: str, target: int = TARGET_EMBEDDING_TOKENS,
    max_tokens: int = MAX_EMBEDDING_TOKENS,
) -> list[str]:
    test = NormalizedChunk(**{**asdict(prototype), "display_text": text, "embedding_text": ""})
    token_count = _token_count(tokenizer, build_embedding_text(test))
    if token_count <= max_tokens:
        return [text]
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n\n+", text) if part.strip()]
    if len(sentences) <= 1:
        return _token_window_split(tokenizer, prototype, text, max_tokens=max_tokens)
    midpoint = len(sentences) // 2
    return _split_for_embedding(tokenizer, prototype, " ".join(sentences[:midpoint]), target, max_tokens) + _split_for_embedding(tokenizer, prototype, " ".join(sentences[midpoint:]), target, max_tokens)


def _split_table_for_embedding(
    tokenizer: Any, prototype: NormalizedChunk, text: str,
    max_tokens: int = MAX_EMBEDDING_TOKENS,
) -> list[str]:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if len(lines) < 3 or "|" not in lines[0]:
        return _split_for_embedding(tokenizer, prototype, text, max_tokens=max_tokens)
    header_count = 2 if re.fullmatch(r"\s*\|?\s*:?-+.*", lines[1]) else 1
    header = lines[:header_count]
    rows = lines[header_count:]
    groups: list[str] = []
    current: list[str] = []
    for row in rows:
        candidate = "\n".join([*header, *current, row])
        test = NormalizedChunk(**{**asdict(prototype), "display_text": candidate, "embedding_text": ""})
        if current and _token_count(tokenizer, build_embedding_text(test)) > TARGET_EMBEDDING_TOKENS:
            groups.append("\n".join([*header, *current]))
            current = [row]
        else:
            current.append(row)
    if current:
        groups.append("\n".join([*header, *current]))
    pieces: list[str] = []
    for group in groups or [text]:
        pieces.extend(_split_for_embedding(tokenizer, prototype, group, max_tokens=max_tokens))
    return pieces


def _split_list_for_embedding(
    tokenizer: Any, prototype: NormalizedChunk, text: str,
    max_tokens: int = MAX_EMBEDDING_TOKENS,
) -> list[str]:
    items = [part.strip() for part in re.split(r"(?m)(?=^\s*(?:[-*•]|\d+[.)])\s+)", text) if part.strip()]
    if len(items) <= 1:
        return _split_for_embedding(tokenizer, prototype, text, max_tokens=max_tokens)
    groups: list[str] = []
    current = ""
    for item in items:
        candidate = f"{current}\n{item}".strip()
        test = NormalizedChunk(**{**asdict(prototype), "display_text": candidate, "embedding_text": ""})
        if current and _token_count(tokenizer, build_embedding_text(test)) > TARGET_EMBEDDING_TOKENS:
            groups.append(current)
            current = item
        else:
            current = candidate
    if current:
        groups.append(current)
    return [
        piece
        for group in groups
        for piece in _split_for_embedding(tokenizer, prototype, group, max_tokens=max_tokens)
    ]


def build_chunks(
    *, document: NormalizedDocument, ingestion_id: str, library_item_id: int | str,
    ontology_id: int | str, book_title: str, rpg_system: str, tokenizer: Any,
    max_embedding_tokens: int = MAX_EMBEDDING_TOKENS,
) -> list[NormalizedChunk]:
    block_map = {block.block_id: block for block in document.blocks}
    child_section_ids = {section.parent_section_id for section in document.sections if section.parent_section_id}
    chunks: list[NormalizedChunk] = []
    chunk_index = 0
    for section in document.sections:
        owned = [block_map[block_id] for block_id in section.block_ids if block_id in block_map]
        evidence = [
            block for block in owned
            if block.block_type not in {"page_header", "page_footer", "heading"}
            and (block.display_text or block.block_type == "picture")
        ]
        if not evidence:
            continue
        display = "\n\n".join(block.display_text for block in evidence if block.display_text).strip()
        if not display:
            continue
        kind = _content_type(evidence, section)
        if section.section_id in child_section_ids and section.heading:
            kind = "section_preamble"
        pages = sorted({page for block in evidence for page in block.page_numbers})
        labels = list(dict.fromkeys(label for block in evidence for label in block.page_labels))
        boxes = [box for block in evidence for box in block.bounding_boxes]
        block_ids = [block.block_id for block in evidence]
        block_types = list(dict.fromkeys(block.block_type for block in evidence))
        parent_id = _stable_id("parent-chunk", ingestion_id, section.section_id)
        parent = NormalizedChunk(
            chunk_id=parent_id, ingestion_id=ingestion_id, library_item_id=library_item_id,
            ontology_id=ontology_id, chunk_role="parent", content_type=kind,
            display_text=display, embedding_text="", book_title=book_title,
            rpg_system=rpg_system, heading_path=section.heading_path,
            primary_heading=section.heading, block_types=block_types,
            physical_page_numbers=pages, displayed_page_labels=labels,
            bounding_boxes=boxes, parent_chunk_id=None,
            parent_section_id=section.section_id, source_block_ids=block_ids,
            chunk_index=chunk_index, embedding_eligible=False,
        )
        chunks.append(parent)
        chunk_index += 1

        # A compact section/RPG entry is best retrieved as one complete child.
        # It keeps every source block together while still preserving the parent
        # relationship for later expansion.
        parent_context = build_embedding_text(parent)
        if _token_count(tokenizer, parent_context) <= max_embedding_tokens:
            child = NormalizedChunk(
                **{
                    **asdict(parent),
                    "chunk_id": _stable_id("child-chunk", ingestion_id, f"{section.section_id}:complete"),
                    "chunk_role": "child",
                    "embedding_text": parent_context,
                    "parent_chunk_id": parent_id,
                    "chunk_index": chunk_index,
                    "embedding_eligible": True,
                }
            )
            chunks.append(child)
            chunk_index += 1
            continue

        # Structure-aware atomic inputs; an oversized parent is recursively split
        # only at paragraphs, list items, table rows, or sentence boundaries.
        child_units = evidence
        for unit in child_units:
            unit_text = unit.display_text
            source_ids = [unit.block_id]
            if unit.block_type == "picture" and not unit_text:
                nearby_captions = [
                    candidate for candidate in evidence
                    if candidate.block_type == "caption"
                    and abs(candidate.reading_order - unit.reading_order) <= 2
                    and (not unit.page_numbers or set(candidate.page_numbers) & set(unit.page_numbers))
                ]
                unit_text = "\n".join(candidate.display_text for candidate in nearby_captions).strip()
                source_ids.extend(candidate.block_id for candidate in nearby_captions)
            if not unit_text:
                continue
            prototype = NormalizedChunk(
                chunk_id="", ingestion_id=ingestion_id, library_item_id=library_item_id,
                ontology_id=ontology_id, chunk_role="child", content_type=(
                    "narrative" if unit.block_type == "paragraph" else _content_type([unit], section)
                ), display_text=unit_text, embedding_text="", book_title=book_title,
                rpg_system=rpg_system, heading_path=section.heading_path,
                primary_heading=section.heading, block_types=[unit.block_type],
                physical_page_numbers=unit.page_numbers,
                displayed_page_labels=unit.page_labels,
                bounding_boxes=unit.bounding_boxes, parent_chunk_id=parent_id,
                parent_section_id=section.section_id, source_block_ids=source_ids,
                chunk_index=chunk_index, embedding_eligible=True,
            )
            if unit.block_type == "table":
                pieces = _split_table_for_embedding(
                    tokenizer, prototype, unit_text, max_embedding_tokens
                )
            elif unit.block_type in {"list", "list_item"}:
                pieces = _split_list_for_embedding(
                    tokenizer, prototype, unit_text, max_embedding_tokens
                )
            else:
                pieces = _split_for_embedding(
                    tokenizer, prototype, unit_text, max_tokens=max_embedding_tokens
                )
            for piece_number, piece in enumerate(pieces):
                child = NormalizedChunk(**{
                    **asdict(prototype),
                    "chunk_id": _stable_id("child-chunk", ingestion_id, f"{unit.block_id}:{piece_number}"),
                    "display_text": piece,
                    "chunk_index": chunk_index,
                })
                child.embedding_text = build_embedding_text(child)
                if _token_count(tokenizer, child.embedding_text) > max_embedding_tokens:
                    # A table row/list item can itself be too large after its
                    # structural splitter. Fall back to tokenizer windows here.
                    fallback_pieces = _token_window_split(
                        tokenizer, prototype, piece, max_tokens=max_embedding_tokens
                    )
                    for fallback_number, fallback_piece in enumerate(fallback_pieces):
                        fallback_child = NormalizedChunk(**{
                            **asdict(prototype),
                            "chunk_id": _stable_id(
                                "child-chunk", ingestion_id,
                                f"{unit.block_id}:{piece_number}:window:{fallback_number}",
                            ),
                            "display_text": fallback_piece,
                            "chunk_index": chunk_index,
                        })
                        fallback_child.embedding_text = build_embedding_text(fallback_child)
                        if _token_count(tokenizer, fallback_child.embedding_text) <= max_embedding_tokens:
                            chunks.append(fallback_child)
                            chunk_index += 1
                    continue
                chunks.append(child)
                chunk_index += 1
    return chunks


def _parse_quality_summary(
    *, native_page_count: int | None, canonical: dict[str, Any], items: list[dict[str, Any]], diagnostics: list[str],
) -> dict[str, Any]:
    """Report parser evidence without inventing failures Docling cannot prove."""
    raw_pages = canonical.get("pages") or {}
    parsed_pages = len(raw_pages)
    pages_with_blocks = {
        int(provenance.get("page_no") or 0)
        for item in items
        for provenance in item.get("provenance", [])
        if int(provenance.get("page_no") or 0) > 0
    }
    tables = [item for item in items if item.get("block_type") == "table"]
    lists = [item for item in items if item.get("block_type") in {"list", "list_item"}]
    empty_tables = sum(not str(item.get("text") or "").strip() for item in tables)
    empty_lists = sum(not str(item.get("text") or "").strip() for item in lists)
    expected_pages = native_page_count or parsed_pages
    return {
        "native_pdf_pages": native_page_count,
        "docling_pages": parsed_pages,
        "pages_without_structured_blocks": max(0, expected_pages - len(pages_with_blocks)),
        "tables_detected": len(tables),
        "tables_without_text": empty_tables,
        "lists_detected": len(lists),
        "lists_without_text": empty_lists,
        "pictures_detected": sum(item.get("block_type") == "picture" for item in items),
        "explicit_parser_diagnostics": len(diagnostics),
    }


