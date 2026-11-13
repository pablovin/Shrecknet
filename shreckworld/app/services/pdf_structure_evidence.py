"""Collect PDF outline and printed-page metadata without parsing page text."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass(slots=True)
class PdfOutlineEvidence:
    evidence_id: str
    title: str
    level: int
    path: list[int]
    reading_order: int
    physical_page: int | None
    x: float | None = None
    y: float | None = None
    destination_kind: str | None = None
    destination_name: str | None = None
    original_reference: str = ""
    diagnostics: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PdfStructureEvidence:
    physical_page_count: int | None
    page_labels: dict[int, str]
    outline: list[PdfOutlineEvidence]
    diagnostics: list[str] = field(default_factory=list)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def collect_pdf_structure(path: Path, *, opener: Callable[[Path], Any] | None = None) -> PdfStructureEvidence:
    """Read outline destinations and page labels only; never inspect page text."""
    diagnostics: list[str] = []
    try:
        if opener is None:
            import pymupdf
            opener = pymupdf.open
        document = opener(path)
    except Exception as exc:
        return PdfStructureEvidence(None, {}, [], [f"pdf_metadata_unavailable:{type(exc).__name__}"])

    try:
        count = len(document)
        labels: dict[int, str] = {}
        for index in range(count):
            page = document[index]
            getter = getattr(page, "get_label", None)
            label = getter() if callable(getter) else None
            if label:
                labels[index + 1] = str(label)
            else:
                diagnostics.append(f"pdf_page_label_missing:{index + 1}")
        entries: list[PdfOutlineEvidence] = []
        levels: list[tuple[int, int]] = []
        try:
            toc = document.get_toc(simple=False) or []
        except Exception as exc:
            toc = []
            diagnostics.append(f"pdf_outline_unavailable:{type(exc).__name__}")
        for order, row in enumerate(toc):
            if not isinstance(row, (list, tuple)) or len(row) < 3:
                diagnostics.append(f"pdf_outline_entry_invalid:{order}")
                continue
            try:
                level = int(row[0])
                title = str(row[1] or "").strip()
                destination_page = int(row[2])
            except (TypeError, ValueError):
                diagnostics.append(f"pdf_outline_entry_invalid:{order}")
                continue
            if not title or level < 1:
                diagnostics.append(f"pdf_outline_entry_invalid:{order}")
                continue
            dest = row[3] if len(row) > 3 and isinstance(row[3], dict) else {}
            kind = dest.get("kind")
            kind_name = str(getattr(kind, "name", kind)) if kind is not None else None
            named = dest.get("nameddest")
            entry_diagnostics: list[str] = []
            physical_page = destination_page if 1 <= destination_page <= count else None
            if (dest.get("uri") or dest.get("file") or kind_name in {"2", "3", "5"}
                    or (kind_name and any(token in kind_name.upper() for token in ("URI", "REMOTE", "LAUNCH")))):
                physical_page = None
                entry_diagnostics.append("non_local_destination")
            elif physical_page is None:
                entry_diagnostics.append("unresolved_or_invalid_destination")
            if named and physical_page is None:
                entry_diagnostics.append("named_destination_not_resolved")
            while levels and levels[-1][0] >= level:
                levels.pop()
            if level > (levels[-1][0] if levels else 0) + 1:
                entry_diagnostics.append("outline_level_gap")
            path = [entry_order for _, entry_order in levels] + [order]
            levels.append((level, order))
            point = dest.get("to")
            x = _number(getattr(point, "x", None))
            y = _number(getattr(point, "y", None))
            entries.append(PdfOutlineEvidence(
                evidence_id=f"pdf-outline-{order}", title=title, level=level, path=path,
                reading_order=order, physical_page=physical_page, x=x, y=y,
                destination_kind=kind_name, destination_name=str(named) if named else None,
                original_reference=str(dest.get("xref") or named or order), diagnostics=entry_diagnostics,
            ))
        if count == 0:
            diagnostics.append("pdf_has_no_pages")
        if not labels:
            diagnostics.append("pdf_page_labels_unavailable")
        if not entries:
            diagnostics.append("pdf_outline_unavailable_or_empty")
        return PdfStructureEvidence(count, labels, entries, diagnostics)
    except Exception as exc:
        return PdfStructureEvidence(None, {}, [], [*diagnostics, f"pdf_metadata_unavailable:{type(exc).__name__}"])
    finally:
        close = getattr(document, "close", None)
        if callable(close):
            close()
