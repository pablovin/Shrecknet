"""Resolve structural evidence into conservative, generation-scoped intervals.

This module reconciles evidence only. Content ownership and chunk projection are
handled by the downstream structure_content and table_text modules.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .pipeline import NormalizedDocument
from .structure_headings import HeadingInferenceResult
from .structure_toc import TocCandidate
from .structure_types import SourcePosition, StructuralEvidence, EvidenceDiagnostic
from .structure_validation import stable_node_id


RESOLVER_VERSION = "book-map-v4-resolver-2"
BOOKMARK_PRIORITY = 3
TOC_PRIORITY = 2
HEADING_PRIORITY = 1


@dataclass(slots=True)
class StructuralCandidate:
    node_id: str
    title: str
    node_type: str
    level: int
    position: SourcePosition
    source_kind: str
    evidence_ids: list[str]
    confidence: float
    resolution_status: str
    parent_id: str | None = None
    child_ids: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    end_exclusive: SourcePosition | None = None
    duplicate_key: str = ""


@dataclass(slots=True)
class StructuralResolution:
    document_id: str
    nodes: list[StructuralCandidate]
    evidence: list[StructuralEvidence]
    diagnostics: list[str]
    conflicts: list[str]
    sufficient_evidence: bool


@dataclass(slots=True)
class _Input:
    title: str
    source_kind: str
    evidence_id: str
    original_reference: str
    position: SourcePosition
    level: int
    confidence: float
    priority: int
    diagnostics: list[str] = field(default_factory=list)


def _title_key(title: str) -> str:
    title = re.sub(r"\b(?:chapter|part|appendix)\s+", "", title, flags=re.I)
    title = re.sub(r"^\s*(?:\d+(?:\.\d+)*|[ivxlcdm]+)[.):]?\s+", "", title, flags=re.I)
    return re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()


def _node_type(title: str, level: int) -> str:
    normalized = title.strip().casefold()
    if re.match(r"^(?:appendix|appendices|annex|supplement)\b", normalized):
        return "appendix"
    if re.match(r"^part\b", normalized):
        return "part"
    if re.match(r"^chapter\b", normalized) or level <= 1:
        return "chapter"
    return "section"


def _position_key(position: SourcePosition) -> tuple[int, int, float, float]:
    return (position.physical_page, position.reading_order,
            position.y if position.y is not None else float("inf"),
            position.x if position.x is not None else float("inf"))


def _to_inputs(*, document: NormalizedDocument, bookmarks: Iterable[object],
               toc_candidates: Iterable[TocCandidate], heading_result: HeadingInferenceResult | None,
               ignore_bookmarks: bool, ignore_toc: bool) -> tuple[list[_Input], list[StructuralEvidence], list[str]]:
    inputs: list[_Input] = []
    evidence: list[StructuralEvidence] = []
    diagnostics: list[str] = []
    page_count = max((page.physical_page_number for page in document.pages), default=0)

    def source_position(title: str, page: int, fallback_order: int, y: float | None = None) -> tuple[SourcePosition, bool]:
        key = _title_key(title)
        matches = [block for block in document.blocks if page in block.page_numbers
                   and _title_key(block.display_text) == key]
        if matches:
            def distance(block):
                box = next((value for value in block.bounding_boxes if value), {})
                block_y = box.get("top")
                return abs(float(block_y) - y) if y is not None and block_y is not None else 0.0
            chosen = min(matches, key=lambda block: (distance(block), block.reading_order, block.block_id))
            box = next((value for value in chosen.bounding_boxes if value), {})
            return SourcePosition(page, chosen.reading_order,
                                  box.get("left"), y if y is not None else box.get("top")), True
        if y is not None:
            on_page = [block for block in document.blocks if page in block.page_numbers
                       and any(box.get("top") is not None for box in block.bounding_boxes)]
            if on_page:
                after = [block for block in on_page if float(next(
                    box["top"] for box in block.bounding_boxes if box.get("top") is not None)) >= y]
                if after:
                    nearest = min(after, key=lambda block: (
                        float(next(box["top"] for box in block.bounding_boxes if box.get("top") is not None)),
                        block.reading_order))
                    return SourcePosition(page, nearest.reading_order, None, y), False
                return SourcePosition(page, max(block.reading_order for block in on_page) + 1, None, y), False
        return SourcePosition(page, fallback_order, None, y), False

    def add(candidate: _Input, observed: list[str]) -> None:
        inputs.append(candidate)
        evidence.append(StructuralEvidence(
            evidence_id=candidate.evidence_id, source_kind=candidate.source_kind,
            original_reference=candidate.original_reference, position=candidate.position,
            observed_features=observed,
            diagnostics=[EvidenceDiagnostic(code=value, message=value, rejected=True)
                         for value in candidate.diagnostics],
        ))

    if not ignore_bookmarks:
        for bookmark in bookmarks:
            title = str(getattr(bookmark, "title", "")).strip()
            page = getattr(bookmark, "physical_page", None)
            if not title or not isinstance(page, int) or page < 1 or (page_count and page > page_count):
                diagnostics.append("invalid_bookmark_not_used")
                continue
            level = max(1, int(getattr(bookmark, "level", 1) or 1))
            evidence_id = str(getattr(bookmark, "evidence_id", "") or f"bookmark-{page}-{getattr(bookmark, 'reading_order', 0)}")
            original = str(getattr(bookmark, "original_reference", evidence_id))
            local_diags = list(getattr(bookmark, "diagnostics", []) or [])
            fallback_order = max(0, int(getattr(bookmark, "reading_order", 0) or 0))
            position, matched = source_position(title, page, fallback_order, getattr(bookmark, "y", None))
            position.x = getattr(bookmark, "x", None) if getattr(bookmark, "x", None) is not None else position.x
            if not matched and position.y is None:
                local_diags.append("bookmark_position_without_body_anchor")
            add(_Input(title, "bookmark", evidence_id, original, position,
                       level, 0.95, BOOKMARK_PRIORITY, local_diags), ["outline_destination", f"outline_level:{level}"])
    else:
        diagnostics.append("bookmarks_ignored_for_evidence_matrix")

    if not ignore_toc:
        toc_rows = list(toc_candidates)
        indents = sorted({toc.indentation for toc in toc_rows if toc.indentation is not None})
        for toc in toc_rows:
            page = toc.mapped_physical_page
            if toc.mapping_status != "mapped" or not isinstance(page, int) or page < 1 or (page_count and page > page_count):
                diagnostics.append(f"toc_entry_unmapped:{toc.evidence_id or toc.source_block_id}")
                continue
            indent_level = indents.index(toc.indentation) + 1 if toc.indentation is not None and indents else 1
            level = max(1, (toc.numbering.count(".") + 1) if toc.numbering and toc.numbering[0].isdigit() else indent_level)
            evidence_id = toc.evidence_id or f"toc-{toc.source_block_id}-{toc.reading_order}"
            position, matched = source_position(toc.title, page, 0)
            local_diags = list(toc.diagnostics)
            if not matched:
                local_diags.append("toc_target_without_body_anchor")
            add(_Input(toc.title, "toc_entry", evidence_id, toc.source_block_id,
                       position, level, toc.confidence, TOC_PRIORITY, local_diags),
                ["printed_contents_entry", f"printed_label:{toc.printed_label}"])
    else:
        diagnostics.append("toc_ignored_for_evidence_matrix")

    if heading_result is not None:
        for heading in heading_result.candidates:
            if heading.disposition != "candidate" or not heading.title.strip() or heading.physical_page is None:
                continue
            evidence_id = f"heading-{heading.block_id}"
            block = next((item for item in document.blocks if item.block_id == heading.block_id), None)
            bbox = next((box for box in block.bounding_boxes if box), {}) if block else {}
            position = SourcePosition(heading.physical_page, heading.reading_order,
                                      bbox.get("left"), bbox.get("top"))
            level = max(1, int(heading.level or 1))
            add(_Input(heading.title, "heading", evidence_id, heading.block_id,
                       position, level, heading.confidence, HEADING_PRIORITY,
                       []), ["global_heading_candidate", *heading.reasons])
    return inputs, evidence, diagnostics


def _same_location(left: _Input, right: _Input) -> bool:
    if left.position.physical_page != right.position.physical_page or _title_key(left.title) != _title_key(right.title):
        return False
    if left.position.y is not None and right.position.y is not None:
        return abs(left.position.y - right.position.y) <= 18
    return left.source_kind != right.source_kind


def _merge(inputs: list[_Input]) -> tuple[list[_Input], dict[str, list[str]], list[str]]:
    remaining = sorted(inputs, key=lambda row: (-row.priority, -row.confidence, _position_key(row.position), row.evidence_id))
    merged: list[_Input] = []
    groups: dict[str, list[str]] = {}
    conflicts: list[str] = []
    while remaining:
        primary = remaining.pop(0)
        group = [primary]
        repeated_same_source = [row for row in remaining if row.source_kind == primary.source_kind
                               and row.position.physical_page == primary.position.physical_page
                               and _title_key(row.title) == _title_key(primary.title)]
        if not repeated_same_source:
            matches = [row for row in remaining if _same_location(primary, row)]
            group.extend(matches)
            for row in matches:
                remaining.remove(row)
        group.sort(key=lambda row: (-row.priority, -row.confidence, row.evidence_id))
        winner = group[0]
        title_variants = { _title_key(row.title) for row in group }
        merged.append(winner)
        groups[winner.evidence_id] = [row.evidence_id for row in group]
        if len(title_variants) > 1:
            conflicts.append(f"candidate_title_conflict:{winner.position.physical_page}:{winner.position.reading_order}")
    return merged, groups, conflicts


def resolve_structure(*, document_id: str, document: NormalizedDocument,
                      bookmarks: Iterable[object] = (), toc_candidates: Iterable[TocCandidate] = (),
                      heading_result: HeadingInferenceResult | None = None,
                      ignore_bookmarks: bool = False, ignore_toc: bool = False) -> StructuralResolution:
    """Reconcile evidence tiers, establish intervals, and assign structural parents."""
    toc_candidates = list(toc_candidates)
    inputs, evidence, diagnostics = _to_inputs(
        document=document, bookmarks=bookmarks, toc_candidates=toc_candidates,
        heading_result=heading_result, ignore_bookmarks=ignore_bookmarks, ignore_toc=ignore_toc,
    )
    bookmark_inputs = [row for row in inputs if row.source_kind == "bookmark" and "non_local_destination" not in row.diagnostics]
    # A shallow but usable outline controls every structural level. Lower tier
    # evidence can corroborate its nodes, but never creates another section.
    bookmark_mode = bool(bookmark_inputs)
    bookmark_inputs.sort(key=lambda row: (_position_key(row.position), row.evidence_id))
    if bookmark_mode:
        groups = {row.evidence_id: [row.evidence_id] for row in bookmark_inputs}
        for row in inputs:
            if row.source_kind == "bookmark":
                continue
            match = next((bookmark for bookmark in bookmark_inputs if _same_location(bookmark, row)), None)
            if match:
                groups[match.evidence_id].append(row.evidence_id)
        merged, evidence_groups, conflicts = bookmark_inputs, groups, []
        diagnostics.append("bookmark_authoritative")
    else:
        merged, evidence_groups, conflicts = _merge(inputs)
        if bookmark_inputs:
            diagnostics.append("invalid_outline_order_fallback")
            merged, evidence_groups, conflicts = _merge([row for row in inputs if row.source_kind != "bookmark"])
    root_id = stable_node_id(document_id, "document", "root")
    pages = max([*(page.physical_page_number for page in document.pages),
                 *(candidate.position.physical_page for candidate in merged)], default=0)
    root = StructuralCandidate(root_id, "Document", "document", 0, SourcePosition(1, 0),
                               "inferred", [], 1.0, "validated", duplicate_key="root")
    output = [root]
    ordered = sorted(merged, key=lambda row: (_position_key(row.position), -row.priority, row.evidence_id))
    stack: list[StructuralCandidate] = [root]
    conflicting_ids: set[str] = set()
    for left_index, left in enumerate(merged):
        for right in merged[left_index + 1:]:
            same_location = left.position.physical_page == right.position.physical_page and (
                (left.position.y is not None and right.position.y is not None and abs(left.position.y - right.position.y) <= 8)
                or (left.position.y is None and right.position.y is None
                    and left.position.reading_order == right.position.reading_order))
            if same_location and left.priority == right.priority and _title_key(left.title) != _title_key(right.title):
                conflicting_ids.update((left.evidence_id, right.evidence_id))
                conflicts.append(f"equal_support_title_conflict:{left.position.physical_page}:{left.position.reading_order}")
    for candidate in ordered:
        title = candidate.title.strip()
        node_type = "contents" if candidate.source_kind == "bookmark" and title.casefold() in {"contents", "table of contents"} else _node_type(title, candidate.level)
        level = max(1, candidate.level)
        if node_type in {"chapter", "part", "appendix", "contents"}:
            level = 1
            stack = [root]
        while len(stack) > 1 and stack[-1].level >= level:
            stack.pop()
        parent = stack[-1]
        if level > parent.level + 1:
            diagnostics.append(f"outline_level_gap:{candidate.evidence_id}")
            level = parent.level + 1
        group_ids = evidence_groups.get(candidate.evidence_id, [candidate.evidence_id])
        conflict_ids = [value for value in group_ids if value != candidate.evidence_id]
        status = "uncertain" if candidate.diagnostics or candidate.evidence_id in conflicting_ids else "resolved"
        node_id = stable_node_id(document_id, node_type, candidate.evidence_id)
        structural_source = "toc" if candidate.source_kind == "toc_entry" else candidate.source_kind
        node = StructuralCandidate(
            node_id, title, node_type, level, candidate.position, structural_source,
            group_ids, candidate.confidence, status, parent.node_id,
            diagnostics=list(candidate.diagnostics), duplicate_key=candidate.evidence_id,
        )
        if conflict_ids:
            node.diagnostics.extend(f"corroborating_evidence:{value}" for value in conflict_ids)
        parent.child_ids.append(node.node_id)
        output.append(node)
        stack.append(node)

    # Printed contents are a document component even when the outline omits it.
    toc_rows = list(toc_candidates) if not ignore_toc else []
    if toc_rows and not any(node.node_type == "contents" for node in output):
        first_page = min(row.physical_page for row in toc_rows)
        first_order = min(row.reading_order for row in toc_rows if row.physical_page == first_page)
        toc_node = StructuralCandidate(
            stable_node_id(document_id, "contents", f"page:{first_page}"), "Contents", "contents", 1,
            SourcePosition(first_page, first_order), "inferred", [], 0.8, "resolved",
            root.node_id, duplicate_key=f"contents:{first_page}",
        )
        output.append(toc_node)
        root.child_ids.append(toc_node.node_id)
    output = [root, *sorted(output[1:], key=lambda node: (_position_key(node.position), node.node_id))]
    output_by_id = {node.node_id: node for node in output}
    for parent in output:
        parent.child_ids.sort(key=lambda node_id: _position_key(output_by_id[node_id].position))
    structural = output[1:]
    for index, node in enumerate(structural):
        boundary = next((following.position for following in structural[index + 1:]
                         if following.level <= node.level), SourcePosition(max(1, pages + 1), 0))
        node.end_exclusive = boundary
    sufficient = bool(structural) and not conflicts and not any(value.startswith("invalid_outline_order") for value in diagnostics)
    if not structural:
        diagnostics.append("no_structural_candidates_root_only")
    return StructuralResolution(document_id, output, evidence, diagnostics, conflicts, sufficient)
