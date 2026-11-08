"""Adapt PyMuPDF layout chunks to the shared normalized document contract."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def validate_parser() -> None:
    import pymupdf

    if not hasattr(pymupdf.Document, "to_chunks"):
        raise RuntimeError("The pymupdf PDF parser requires PyMuPDF 2.1 or later and Python 3.11+")


def parse_pdf(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    import pymupdf

    with pymupdf.open(path) as pdf:
        if not hasattr(pdf, "to_chunks"):
            raise RuntimeError("The pymupdf PDF parser requires PyMuPDF 2.1 or later and Python 3.11+")
        pages = {
            str(index + 1): {"size": {"width": page.rect.width, "height": page.rect.height}}
            for index, page in enumerate(pdf)
        }
        result = pdf.to_chunks(use_ocr=False, table_mode="isolate", merge_small_chunks=False)
        if result.diagnostics["page_count"] != len(pdf):
            raise ValueError("PyMuPDF did not parse every PDF page")
        sections = {section.id: section for section in result.sections}
        elements = {element.id: element for element in result.elements}
        items: list[dict[str, Any]] = []
        current_path: list[str] = []
        current_section_id: str | None = None
        for chunk in result:
            metadata = chunk.metadata
            section = sections.get(metadata.section_id)
            path_titles = list(metadata.section_path)
            common = 0
            while common < min(len(current_path), len(path_titles)) and current_path[common] == path_titles[common]:
                common += 1
            if path_titles and current_section_id != metadata.section_id and common == len(path_titles) == len(current_path):
                common -= 1
            if common < len(current_path) and common == len(path_titles) and path_titles:
                # Re-entering an ancestor must close the previous nested section.
                common -= 1
            for index in range(common, len(path_titles)):
                heading = path_titles[index]
                heading_element = elements.get(section.heading_element_id) if section and index == len(path_titles) - 1 else None
                page_number = heading_element.page if heading_element else metadata.page_start
                items.append({
                    "reference": f"heading-{chunk.id}-{index}", "block_type": "heading",
                    "text": heading, "level": index + 1, "reading_order": len(items),
                    "provenance": [{"page_no": page_number}],
                })
            current_path = path_titles
            current_section_id = metadata.section_id
            text = chunk.text.strip()
            if section:
                # The section title is represented by the heading block, not evidence.
                text = re.sub(r"^#{1,6}\s+(?:\*\*)?" + re.escape(section.title) + r"(?:\*\*)?\s*(?:\n|$)", "", text, count=1).strip()
            if not text:
                continue
            provenance = []
            for box in metadata.bboxes:
                page, left, top, right, bottom = box
                provenance.append({"page_no": page, "bbox": {
                    "left": left, "top": top, "right": right, "bottom": bottom, "coord_origin": "TOPLEFT",
                }})
            if not provenance:
                provenance = [{"page_no": number} for number in range(metadata.page_start, metadata.page_end + 1)]
            kind = "table" if "table" in metadata.types else "list" if "list" in metadata.types else "paragraph"
            items.append({
                "reference": chunk.id, "block_type": kind, "text": text,
                "reading_order": len(items), "provenance": provenance,
                "chunk_metadata": {"element_ids": list(metadata.element_ids), "table_ids": list(metadata.table_ids),
                                   "figure_ids": list(metadata.figure_ids), "types": list(metadata.types)},
            })
        diagnostics = []
        for key in ("pages_without_chunks", "zero_chunk_causes", "figures_without_text", "degenerate_tables"):
            value = result.diagnostics.get(key)
            if value:
                diagnostics.append(f"PyMuPDF {key}: {value}")
        return {"pages": pages}, items, diagnostics
