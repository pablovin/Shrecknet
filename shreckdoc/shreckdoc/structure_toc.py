"""Extract visible table-of-contents candidates and conservative page mappings.

This module proposes entries only. It never mutates document blocks or assigns
structural parents; ambiguous entries and mappings retain their reasons.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from .pipeline import NormalizedDocument, NormalizedBlock


_LABEL = r"(?:[0-9]{1,4}|[ivxlcdm]{1,12}|[a-z])(?:\s*[-–]\s*(?:[0-9]{1,4}|[ivxlcdm]{1,12}))?"
_ROW = re.compile(rf"^(?P<title>.+?)\s*(?P<leader>(?:\.{{2,}}|…+|·{{2,}}|\s{{2,}}))\s*(?P<label>{_LABEL})\s*[.)]?$", re.I)
_NUMBERED_ROW = re.compile(rf"^(?P<title>(?:chapter\s+)?(?:\d+(?:\.\d+)*|[ivxlcdm]+)[.):]?\s+.+?)\s+(?P<label>{_LABEL})\s*[.)]?$", re.I)
_ROMAN = re.compile(r"^[ivxlcdm]+$", re.I)


@dataclass(slots=True)
class TocCandidate:
    title: str
    printed_label: str
    physical_page: int
    reading_order: int
    source_block_id: str
    column: int
    evidence_id: str = ""
    source_line_index: int = 0
    supporting_block_ids: list[str] = field(default_factory=list)
    indentation: float | None = None
    numbering: str | None = None
    confidence: float = 0.0
    diagnostics: list[str] = field(default_factory=list)
    mapped_physical_page: int | None = None
    mapping_status: str = "unresolved"
    mapping_reason: str = "not_mapped"


@dataclass(slots=True)
class TocExtraction:
    candidates: list[TocCandidate]
    diagnostics: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PageMapping:
    printed_label: str
    physical_page: int | None
    status: str
    reason: str
    supporting_titles: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PageMappingResult:
    candidates: list[TocCandidate]
    mappings: list[PageMapping]
    diagnostics: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _Line:
    text: str
    block: NormalizedBlock
    y: float | None
    x: float | None
    column: int = 0
    line_index: int = 0


def _box(block: NormalizedBlock) -> tuple[float | None, float | None, float | None]:
    box = next((row for row in block.bounding_boxes if row), {})
    try:
        return float(box["left"]), float(box["top"]), float(box["right"])
    except (KeyError, TypeError, ValueError):
        return None, None, None


def _columnize(lines: list[_Line]) -> None:
    starts = sorted({line.x for line in lines if line.x is not None})
    if len(starts) < 2:
        return
    gaps = [(starts[index + 1] - starts[index], index) for index in range(len(starts) - 1)]
    gap, split = max(gaps, default=(0.0, 0))
    if gap < 0.08:
        return
    threshold = (starts[split] + starts[split + 1]) / 2
    left_count = sum(1 for line in lines if line.x is not None and line.x <= threshold)
    right_count = sum(1 for line in lines if line.x is not None and line.x > threshold)
    if left_count < 2 or right_count < 2:
        return
    for line in lines:
        if line.x is not None:
            line.column = int(line.x > threshold)


def _line_candidate(text: str) -> tuple[str, str, bool] | None:
    text = re.sub(r"\s+", " ", text).strip()
    match = _ROW.match(text) or _NUMBERED_ROW.match(text)
    if not match:
        return None
    title = match.group("title").strip(" .·…\t")
    label = match.group("label").strip(" .)")
    if (not title or len(title) > 240 or re.search(r"(?:\.{2,}|…{2,}|·{2,})", title)
            or re.search(r"[.!?;]$", title)):
        return None
    return title, label, bool(match.groupdict().get("leader"))


def extract_toc_candidates(document: NormalizedDocument) -> TocExtraction:
    """Find printed contents rows across every page, preserving source positions."""
    by_page: dict[int, list[_Line]] = {}
    page_widths = {page.physical_page_number: page.width for page in document.pages}
    for block in document.blocks:
        if block.block_type not in {"paragraph", "heading", "list", "list_item", "unknown"} or not block.page_numbers:
            continue
        x0, y0, _ = _box(block)
        page = min(block.page_numbers)
        width = page_widths.get(page)
        if x0 is not None and width:
            x0 /= width
        for line_index, line in enumerate(block.display_text.splitlines()):
            if line.strip():
                by_page.setdefault(page, []).append(_Line(line.strip(), block, y0, x0, line_index=line_index))

    output: list[TocCandidate] = []
    diagnostics: list[str] = []
    for page, lines in sorted(by_page.items()):
        _columnize(lines)
        lines.sort(key=lambda line: (line.column, line.y if line.y is not None else line.block.reading_order,
                                     line.block.reading_order))
        raw: list[tuple[str, str, _Line, bool, list[_Line]]] = []
        pending: list[_Line] = []
        for line in lines:
            found = _line_candidate(line.text)
            if found:
                title, label, leader = found
                supporting_lines = list(pending)
                if pending:
                    title = " ".join([part.text.strip() for part in pending] + [title]).strip()
                    pending.clear()
                raw.append((title, label, line, leader, supporting_lines))
            elif (len(line.text) < 100 and not re.search(r"[.!?]$", line.text)
                  and not re.match(r"^(?:contents|table of contents|index)$", line.text, re.I)):
                if pending and pending[-1].column != line.column:
                    pending.clear()
                pending.append(line)
                if len(pending) > 2:
                    pending.pop(0)
            else:
                pending.clear()
        # Dotted leaders are strong row evidence; undotted page-number rows only
        # count on a page that independently resembles a contents list.
        strong_count = sum(1 for _, _, _, leader, _ in raw if leader)
        page_is_contents = strong_count >= 1 or len(raw) >= 3
        if not page_is_contents:
            continue
        for title, label, line, leader, supporting_lines in raw:
            x0, _, _ = _box(line.block)
            numbering = re.match(r"^((?:chapter\s+)?(?:\d+(?:\.\d+)*|[ivxlcdm]+)[.):]?)\s+", title, re.I)
            output.append(TocCandidate(
                title=title, printed_label=label, physical_page=page,
                reading_order=line.block.reading_order, source_block_id=line.block.block_id,
                column=line.column, evidence_id=f"toc-{page}-{line.block.block_id}-{line.line_index}",
                source_line_index=line.line_index,
                supporting_block_ids=list(dict.fromkeys(part.block.block_id for part in [*supporting_lines, line])),
                indentation=x0, numbering=numbering.group(1) if numbering else None,
                confidence=0.88 if leader else 0.65,
            ))
    if not output:
        diagnostics.append("toc_candidates_not_found")
    return TocExtraction(output, diagnostics)


def _label_value(label: str) -> tuple[str, int] | None:
    value = label.strip().casefold()
    if value.isdecimal():
        return "arabic", int(value)
    if not _ROMAN.fullmatch(value):
        return None
    values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    total = previous = 0
    for char in reversed(value):
        amount = values[char]
        total += -amount if amount < previous else amount
        previous = max(previous, amount)
    # Reject malformed roman numerals instead of interpreting them loosely.
    def to_roman(number: int) -> str:
        pairs = ((1000,"m"),(900,"cm"),(500,"d"),(400,"cd"),(100,"c"),(90,"xc"),(50,"l"),(40,"xl"),(10,"x"),(9,"ix"),(5,"v"),(4,"iv"),(1,"i"))
        result = ""
        for amount, numeral in pairs:
            count, number = divmod(number, amount)
            result += numeral * count
        return result
    return ("roman", total) if total > 0 and to_roman(total) == value else None


def _title_key(title: str) -> str:
    title = re.sub(r"\bchapter\s+", "", title, flags=re.I)
    title = re.sub(r"^\s*(?:\d+(?:\.\d+)*|[ivxlcdm]+)[.):]?\s+", "", title, flags=re.I)
    return re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()


def map_printed_pages(candidates: Iterable[TocCandidate], headings: Iterable[object]) -> PageMappingResult:
    """Map labels only from unique title anchors or corroborated numeric offsets."""
    entries = list(candidates)
    heading_rows = list(headings)
    by_title: dict[str, list[object]] = {}
    for heading in heading_rows:
        title = str(getattr(heading, "title", getattr(heading, "text", "")))
        if title:
            by_title.setdefault(_title_key(title), []).append(heading)
    label_counts = Counter(entry.printed_label.casefold() for entry in entries)
    anchors: list[tuple[str, str, int, str]] = []
    mappings: dict[str, PageMapping] = {}
    for entry in entries:
        matches = by_title.get(_title_key(entry.title), [])
        if len(matches) == 1 and getattr(matches[0], "physical_page", None) is not None:
            physical = int(getattr(matches[0], "physical_page"))
            if label_counts[entry.printed_label.casefold()] == 1:
                anchors.append((entry.printed_label, "", physical, entry.title))
            entry.mapped_physical_page = physical
            entry.mapping_status, entry.mapping_reason = "mapped", "unique_title_anchor"
            mappings[entry.printed_label.casefold()] = PageMapping(entry.printed_label, physical, "mapped", "unique_title_anchor", [entry.title])
        elif len(matches) > 1:
            entry.mapping_status, entry.mapping_reason = "ambiguous", "duplicate_heading_title"
            mappings[entry.printed_label.casefold()] = PageMapping(entry.printed_label, None, "ambiguous", "duplicate_heading_title", [entry.title])
        else:
            entry.mapping_status, entry.mapping_reason = "unresolved", "no_heading_anchor"

    # An offset is trusted only when at least two unique heading anchors agree
    # within the same numbering family. Roman and Arabic page-label resets stay separate.
    offsets: dict[str, list[tuple[int, int, str]]] = {}
    for label, _, physical, title in anchors:
        parsed = _label_value(label)
        if parsed:
            family, printed = parsed
            offsets.setdefault(family, []).append((physical - printed, printed, title))
    corroborated: dict[str, list[tuple[int, int, int]]] = {}
    for family, rows in offsets.items():
        for offset, count in Counter(row[0] for row in rows).items():
            agreeing = [row[1] for row in rows if row[0] == offset]
            if count >= 2:
                corroborated.setdefault(family, []).append((offset, min(agreeing), max(agreeing)))
    for entry in entries:
        if entry.mapping_status == "mapped":
            continue
        parsed = _label_value(entry.printed_label)
        if parsed:
            family, printed = parsed
            matching_offsets = [row for row in corroborated.get(family, []) if row[1] <= printed <= row[2]]
            if len(matching_offsets) == 1:
                offset_row = matching_offsets[0]
                offset = offset_row[0]
                mapped_page = printed + offset
                entry.mapped_physical_page = mapped_page
                entry.mapping_status, entry.mapping_reason = "mapped", "corroborated_piecewise_offset"
                mappings[entry.printed_label.casefold()] = PageMapping(entry.printed_label, mapped_page, "mapped", "corroborated_piecewise_offset")
                continue
            if len(matching_offsets) > 1:
                entry.mapping_status, entry.mapping_reason = "ambiguous", "conflicting_piecewise_offsets"
        if entry.mapping_status != "ambiguous":
            mappings.setdefault(entry.printed_label.casefold(), PageMapping(entry.printed_label, None, "unresolved", entry.mapping_reason))
    labels: dict[str, list[TocCandidate]] = defaultdict(list)
    for entry in entries:
        labels[entry.printed_label.casefold()].append(entry)
    for label, rows in labels.items():
        mapped_pages = {row.mapped_physical_page for row in rows if row.mapped_physical_page is not None}
        if len(rows) > 1:
            for row in rows:
                row.mapped_physical_page = None
                row.mapping_status, row.mapping_reason = "ambiguous", "duplicate_printed_label"
            mappings[label] = PageMapping(rows[0].printed_label, None, "ambiguous", "duplicate_printed_label",
                                          [row.title for row in rows])
    return PageMappingResult(entries, list(mappings.values()), [])
