"""Adapt PyMuPDF layout chunks to the shared normalized document contract."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

def validate_parser() -> None:
    import pymupdf

    if not hasattr(pymupdf.Document, "to_chunks"):
        raise RuntimeError("The pymupdf PDF parser requires PyMuPDF 2.1 or later and Python 3.11+")


def _values(value: Any) -> list[Any]:
    if value is None:
        return []
    try:
        return list(value)
    except TypeError:
        return []


def _bbox(value: Any) -> list[float] | None:
    if value is None:
        return None
    try:
        coordinates = [float(part) for part in value]
    except (TypeError, ValueError):
        return None
    return coordinates if len(coordinates) == 4 else None


def _element_evidence(element: Any) -> dict[str, Any]:
    """Serialize the documented element view without assuming paragraph boundaries."""
    result: dict[str, Any] = {}
    for key in ("id", "page", "text"):
        value = getattr(element, key, None)
        if isinstance(value, (str, int, float, bool)):
            result[key] = value
    box_class = getattr(element, "boxclass", None)
    if box_class is None:
        box_class = getattr(element, "box_class", None)
    if box_class is not None:
        result["box_class"] = str(getattr(box_class, "value", box_class))
    bounding_box = _bbox(getattr(element, "bbox", None))
    if bounding_box is not None:
        result["bbox"] = bounding_box
    header_footer = getattr(element, "is_header_footer", None)
    if isinstance(header_footer, bool):
        result["is_header_footer"] = header_footer
    native_fields: dict[str, str | int | float | bool] = {}
    raw_fields = getattr(element, "__dict__", {})
    if isinstance(raw_fields, dict):
        for key, value in raw_fields.items():
            lowered = str(key).lower()
            if any(part in lowered for part in ("image", "path", "uri", "bytes")):
                continue
            if isinstance(value, (str, int, float, bool)):
                native_fields[str(key)] = value
    if native_fields:
        result["native_fields"] = native_fields
        if "is_header_footer" not in result:
            flags = [value for key, value in native_fields.items()
                     if ("header" in key.lower() or "footer" in key.lower()) and isinstance(value, bool)]
            if any(flags):
                result["is_header_footer"] = True
    return result


def _view_evidence(view: Any, kind: str) -> dict[str, Any]:
    """Keep stable public table/figure fields; omit image bytes and native handles."""
    fields = ("id", "element_id", "page", "section_id", "chunk_id", "caption",
              "caption_element_id", "markdown", "html", "text", "ocr_text", "placeholder",
              "boxclass", "headers", "has_text")
    result: dict[str, Any] = {"kind": kind}
    for key in fields:
        value = getattr(view, key, None)
        if value is None or isinstance(value, bytes):
            continue
        if key == "headers":
            result[key] = [str(entry) for entry in _values(value)]
        elif key == "has_text":
            result[key] = bool(value)
        elif isinstance(value, (str, int, float, bool)):
            result[key] = value
    bounding_box = _bbox(getattr(view, "bbox", None))
    if bounding_box is not None:
        result["bbox"] = bounding_box
    return result


def _provenance(page: Any, bbox: Any = None) -> list[dict[str, Any]]:
    if not isinstance(page, int) or page < 1:
        return []
    result: dict[str, Any] = {"page_no": page}
    coordinates = _bbox(bbox)
    if coordinates:
        left, top, right, bottom = coordinates
        result["bbox"] = {"left": left, "top": top, "right": right, "bottom": bottom,
                          "coord_origin": "TOPLEFT"}
    return [result]


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
        native_sections = _values(getattr(result, "sections", None))
        sections = {section.id: section for section in native_sections if getattr(section, "id", None)}
        elements = {element.id: element for element in _values(getattr(result, "elements", None))
                    if getattr(element, "id", None)}
        tables = {table.id: table for table in getattr(result, "tables", []) if getattr(table, "id", None)}
        figures = {figure.id: figure for figure in getattr(result, "figures", []) if getattr(figure, "id", None)}
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
                path_section = next((candidate for candidate in native_sections
                                     if list(getattr(candidate, "path", [])) == path_titles[:index + 1]), None)
                heading_element_id = getattr(path_section, "heading_element_id", None) if path_section else None
                heading_element = elements.get(heading_element_id) if heading_element_id else None
                page_number = heading_element.page if heading_element else metadata.page_start
                items.append({
                    "reference": heading_element_id or (path_section.id if path_section else f"heading-{chunk.id}-{index}"),
                    "block_type": "heading",
                    "text": heading, "level": index + 1, "reading_order": len(items),
                    "provenance": [{"page_no": page_number}],
                    "native_type": "section_heading",
                    "native_metadata": {"section_id": getattr(path_section, "id", None),
                                        "heading_element_id": heading_element_id,
                                        "element": _element_evidence(heading_element) if heading_element else None},
                })
            current_path = path_titles
            current_section_id = metadata.section_id
            text = chunk.text.strip()
            if section:
                # The section title is represented by the heading block, not evidence.
                text = re.sub(r"^#{1,6}\s+(?:\*\*)?" + re.escape(section.title) + r"(?:\*\*)?\s*(?:\n|$)", "", text, count=1).strip()
            provenance = []
            for box in metadata.bboxes:
                page, left, top, right, bottom = box
                provenance.append({"page_no": page, "bbox": {
                    "left": left, "top": top, "right": right, "bottom": bottom, "coord_origin": "TOPLEFT",
                }})
            if not provenance:
                provenance = [{"page_no": number} for number in range(metadata.page_start, metadata.page_end + 1)]
            native_types = list(metadata.types)
            normalized_types = {str(value).lower() for value in native_types}
            if "table" in metadata.table_ids and not normalized_types:
                kind = "table"
            elif normalized_types == {"table"}:
                kind = "table"
            elif normalized_types == {"list"}:
                kind = "list"
            elif normalized_types and normalized_types <= {"heading", "paragraph"}:
                kind = "paragraph"
            else:
                kind = "unknown"
            element_evidence = [_element_evidence(elements[element_id]) for element_id in metadata.element_ids
                                if element_id in elements]
            table_evidence = [_view_evidence(tables[table_id], "table") for table_id in metadata.table_ids
                              if table_id in tables]
            figure_evidence = [_view_evidence(figures[figure_id], "figure") for figure_id in metadata.figure_ids
                               if figure_id in figures]
            if text or metadata.table_ids:
                items.append({
                    "reference": chunk.id, "block_type": kind, "text": text,
                    "reading_order": len(items), "provenance": provenance,
                    "native_type": ",".join(native_types) or ("table" if metadata.table_ids else "unknown"),
                    "native_metadata": {"elements": element_evidence, "tables": table_evidence,
                                        "figures": figure_evidence},
                    "chunk_metadata": {"element_ids": list(metadata.element_ids), "table_ids": list(metadata.table_ids),
                                       "figure_ids": list(metadata.figure_ids), "types": native_types},
                })
            for figure_id in metadata.figure_ids:
                figure = figures.get(figure_id)
                if figure is None:
                    continue
                figure_text = getattr(figure, "ocr_text", None)
                figure_provenance = _provenance(getattr(figure, "page", None), getattr(figure, "bbox", None))
                items.append({
                    "reference": str(figure_id), "block_type": "picture", "text": "",
                    "reading_order": len(items), "provenance": figure_provenance,
                    "native_type": str(getattr(figure, "boxclass", None) or "figure"),
                    "native_metadata": {"figure": _view_evidence(figure, "figure"),
                                        "text_available": bool(figure_text and str(figure_text).strip())},
                    "chunk_metadata": {"element_ids": [getattr(figure, "element_id", "")] if getattr(figure, "element_id", None) else [],
                                       "table_ids": [], "figure_ids": [str(figure_id)], "types": ["figure"]},
                })

        # Header/footer elements remain source evidence even when PyMuPDF excludes
        # them from retrieval chunks. Only explicit parser flags classify them.
        seen_elements = {element_id for chunk in result for element_id in chunk.metadata.element_ids}
        for element_id, element in elements.items():
            evidence = _element_evidence(element)
            if not evidence.get("is_header_footer") or element_id in seen_elements:
                continue
            page = evidence.get("page")
            kind = str(evidence.get("box_class") or "page_header").lower()
            if not evidence.get("box_class"):
                native_fields = evidence.get("native_fields", {})
                if isinstance(native_fields, dict):
                    kind = next((str(value).lower() for key, value in native_fields.items()
                                 if key.lower() in {"boxclass", "box_class", "class", "kind"}
                                 and isinstance(value, str)), kind)
            block_type = "page_footer" if "footer" in kind else "page_header"
            items.append({"reference": element_id, "block_type": block_type,
                          "text": str(evidence.get("text") or ""), "reading_order": len(items),
                          "provenance": _provenance(page, evidence.get("bbox")),
                          "native_type": str(evidence.get("box_class") or "unknown"),
                          "native_metadata": {"element": evidence},
                          "chunk_metadata": {"element_ids": [element_id], "table_ids": [],
                                             "figure_ids": [], "types": [block_type]}})
        diagnostics = []
        for key in ("pages_without_chunks", "zero_chunk_causes", "figures_without_text", "degenerate_tables"):
            value = result.diagnostics.get(key)
            if value:
                diagnostics.append(f"PyMuPDF {key}: {value}")
        return {"pages": pages}, items, diagnostics
