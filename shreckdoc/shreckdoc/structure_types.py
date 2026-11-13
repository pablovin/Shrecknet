"""Storage-independent source evidence types shared by document adapters.

These records describe what an extractor observed. They do not decide structural
parentage or claim that source text was completely recognized.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


StructuralSource = Literal["bookmark", "toc", "heading", "inferred"]
EvidenceKind = Literal["bookmark", "toc_entry", "heading", "block", "page"]
PageCoverage = Literal["extracted", "confirmed_blank", "unknown"]


@dataclass(slots=True)
class SourcePosition:
    physical_page: int
    reading_order: int
    x: float | None = None
    y: float | None = None


@dataclass(slots=True)
class SourceSpan:
    start: int | None = None
    end: int | None = None


@dataclass(slots=True)
class EvidenceDiagnostic:
    code: str
    message: str
    rejected: bool = False


@dataclass(slots=True)
class StructuralEvidence:
    evidence_id: str
    source_kind: EvidenceKind
    original_reference: str
    position: SourcePosition
    parser_name: str | None = None
    parser_version: str | None = None
    observed_features: list[str] = field(default_factory=list)
    diagnostics: list[EvidenceDiagnostic] = field(default_factory=list)


@dataclass(slots=True)
class TableCell:
    text: str
    row: int
    column: int
    row_span: int = 1
    column_span: int = 1
    is_header: bool = False
    header_path: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TableStructure:
    cells: list[TableCell] = field(default_factory=list)
    source_reference: str | None = None
    structure_quality: Literal["structured", "text_fallback", "unreadable"] = "structured"


@dataclass(slots=True)
class NormalizedPageAccounting:
    physical_page_number: int
    coverage: PageCoverage = "unknown"
    evidence_ids: list[str] = field(default_factory=list)
    note: str | None = None
