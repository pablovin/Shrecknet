"""Deterministic table value serialization and bounded header-preserving groups."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .structure_types import TableCell, TableStructure


@dataclass(slots=True)
class SerializedTable:
    text: str
    quality: str
    row_texts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    row_groups: list[list[str]] = field(default_factory=list)


def _cell_header(cell: TableCell, headers: list[TableCell]) -> str:
    if cell.header_path:
        parts = list(cell.header_path)
    else:
        covered = range(cell.column, cell.column + max(1, cell.column_span))
        parts = [header.text for header in sorted(headers, key=lambda item: (item.row, item.column))
                 if header.column in covered and header.text.strip()]
    if cell.is_row_header and cell.text.strip():
        parts.append(cell.text.strip())
    return " / ".join(part.strip() for part in parts if part.strip()) or f"Column {cell.column + 1}"


def serialize_table_text(structure: TableStructure | None, fallback_text: str = "") -> SerializedTable:
    """Serialize actual cells as stable Header/Value rows; missing cells stay absent."""
    if structure is None or not structure.cells or structure.structure_quality != "structured":
        quality = structure.structure_quality if structure else "text_fallback"
        return SerializedTable(fallback_text.strip(), quality, [], ["structured_table_cells_unavailable"])
    headers = [cell for cell in structure.cells if cell.is_header or cell.row == 0]
    data_cells = [cell for cell in structure.cells if cell not in headers and cell.text.strip()]
    if not data_cells:
        header_texts = [cell.text.strip() for cell in sorted(headers, key=lambda item: (item.row, item.column)) if cell.text.strip()]
        if not header_texts:
            return SerializedTable(fallback_text.strip(), "unreadable", [], ["table_has_no_serializable_values"])
        text = "| Header | Value |\n| --- | --- |\n" + "\n".join(f"| {_escape(value)} |  |" for value in header_texts)
        rows = text.splitlines()[2:]
        return SerializedTable(text, "structured", rows, row_groups=[[row] for row in rows])
    row_groups: list[list[str]] = []
    cells_by_row: dict[int, list[TableCell]] = {}
    for cell in sorted(data_cells, key=lambda item: (item.row, item.column)):
        cells_by_row.setdefault(cell.row, []).append(cell)
    for cells in cells_by_row.values():
        group = []
        for cell in cells:
            header = _cell_header(cell, headers)
            group.append(f"| {_escape(header)} | {_escape(cell.text.strip())} |")
        row_groups.append(group)
    row_texts = [row for group in row_groups for row in group]
    text = "| Header | Value |\n| --- | --- |\n" + "\n".join(row_texts)
    return SerializedTable(text, "structured", row_texts, row_groups=row_groups)


def bounded_table_row_groups(serialized: SerializedTable, *, count_tokens: Callable[[str], int],
                             max_tokens: int = 400) -> list[str]:
    """Group complete table rows under a caller-supplied contextual token counter."""
    if max_tokens < 1:
        raise ValueError("max_tokens must be positive")
    lines = [line for line in serialized.text.splitlines() if line.strip()]
    if len(lines) < 3 or not lines[0].lstrip().startswith("|"):
        return [serialized.text] if serialized.text else []
    header = lines[:2]
    rows = serialized.row_groups or [[line] for line in lines[2:]]
    groups: list[str] = []
    current: list[str] = []
    for row_group in rows:
        candidate = "\n".join([*header, *current, *row_group])
        if current and count_tokens(candidate) > max_tokens:
            groups.append("\n".join([*header, *current]))
            current = list(row_group)
        else:
            current.extend(row_group)
    if current:
        groups.append("\n".join([*header, *current]))
    return groups or [serialized.text]


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
