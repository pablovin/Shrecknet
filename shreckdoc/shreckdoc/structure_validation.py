"""Validation and traversal for the resolved Book Map tree."""
from __future__ import annotations

from collections import Counter
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5


class StructureValidationError(ValueError):
    """Raised when a resolved map violates its published tree invariants."""


class _PositionLike(Protocol):
    physical_page: int
    reading_order: int


class _PageRangeLike(Protocol):
    start: int
    end: int


class _NodeLike(Protocol):
    id: str
    document_id: str
    node_type: str
    level: int
    parent_id: str | None
    child_ids: list[str]
    source_block_ids: list[str]
    evidence_ids: list[str]
    structural_node_id: str | None
    position: _PositionLike
    pages: _PageRangeLike | None
    page_locators: list[_PageLocatorLike]


class _PageLocatorLike(Protocol):
    physical_page: int


class _EvidenceLike(Protocol):
    id: str
    locator: _PositionLike


class _QualityLike(Protocol):
    node_count: int
    source_block_count: int
    owned_block_count: int


class BookMapLike(Protocol):
    document_id: str
    root_id: str
    nodes: list[_NodeLike]
    evidence: list[_EvidenceLike]
    quality: _QualityLike


def stable_node_id(document_id: str, node_type: str, source_identity: str) -> str:
    """Return an order-independent node ID scoped to one document generation."""
    if not document_id or not node_type or not source_identity:
        raise ValueError("document, node type and source identity are required")
    return str(uuid5(NAMESPACE_URL, f"shreckworld:book-map-v4:{document_id}:{node_type}:{source_identity}"))


def descendants(tree: BookMapLike, node_id: str, *, include_self: bool = False) -> list[str]:
    """Return descendant IDs in canonical child order, safely rejecting cycles."""
    by_id = {node.id: node for node in tree.nodes}
    if node_id not in by_id:
        raise KeyError(node_id)
    result: list[str] = []
    active: set[str] = set()
    stack: list[tuple[str, bool]] = [(node_id, False)]
    while stack:
        current_id, leaving = stack.pop()
        if leaving:
            active.remove(current_id)
            continue
        if current_id in active:
            raise StructureValidationError(f"cycle detected at node {current_id}")
        active.add(current_id)
        if current_id != node_id or include_self:
            result.append(current_id)
        stack.append((current_id, True))
        for child_id in reversed(by_id[current_id].child_ids):
            if child_id not in by_id:
                raise StructureValidationError(f"missing child node {child_id}")
            stack.append((child_id, False))
    return result


def validate_book_map(tree: BookMapLike) -> list[str]:
    """Return invariant violations; an empty list means the tree is publishable."""
    errors: list[str] = []
    by_id = {node.id: node for node in tree.nodes}
    if len(by_id) != len(tree.nodes):
        errors.append("node IDs must be unique")
    if tree.root_id not in by_id:
        return [*errors, "root_id must reference a node in the map"]

    roots = [node.id for node in tree.nodes if node.parent_id is None]
    if roots != [tree.root_id] and set(roots) != {tree.root_id}:
        errors.append("map must have exactly one root matching root_id")
    root = by_id[tree.root_id]
    if root.parent_id is not None:
        errors.append("root node cannot have a parent")
    if root.node_type != "document":
        errors.append("root node must be a document")
    if root.level != 0:
        errors.append("root node level must be zero")

    block_owners = Counter(block_id for node in tree.nodes for block_id in node.source_block_ids)
    duplicate_blocks = sorted(block_id for block_id, count in block_owners.items() if count > 1)
    if duplicate_blocks:
        errors.append(f"source blocks must have one owner: {', '.join(duplicate_blocks)}")

    structural_types = {"document", "part", "chapter", "section", "appendix"}
    evidence_ids = [evidence.id for evidence in tree.evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        errors.append("evidence IDs must be unique")
    known_evidence_ids = set(evidence_ids)
    for node in tree.nodes:
        if node.document_id != tree.document_id:
            errors.append(f"node {node.id} references a different document generation")
        if len(node.child_ids) != len(set(node.child_ids)):
            errors.append(f"node {node.id} has duplicate child IDs")
        for child_id in node.child_ids:
            child = by_id.get(child_id)
            if child is None:
                errors.append(f"node {node.id} references missing child {child_id}")
            elif child.parent_id != node.id:
                errors.append(f"child link {node.id} -> {child_id} is not reciprocal")
        if node.parent_id is not None:
            parent = by_id.get(node.parent_id)
            if parent is None:
                errors.append(f"node {node.id} references missing parent {node.parent_id}")
            elif parent.level >= node.level:
                errors.append(f"node {node.id} must have a level greater than its parent")
            elif parent.pages is not None and node.pages is not None:
                if node.pages.start < parent.pages.start or node.pages.end > parent.pages.end:
                    errors.append(f"node {node.id} page range falls outside its parent")
        if node.structural_node_id is not None:
            owner = by_id.get(node.structural_node_id)
            if owner is None or owner.node_type not in structural_types:
                errors.append(f"node {node.id} has an invalid structural owner")
            elif node.node_type not in structural_types and node.structural_node_id != node.parent_id:
                parent = by_id.get(node.parent_id) if node.parent_id else None
                if parent is None or node.structural_node_id not in {parent.id, parent.structural_node_id}:
                    errors.append(f"node {node.id} structural owner is outside its parent chain")
        if any(evidence_id not in known_evidence_ids for evidence_id in node.evidence_ids):
            errors.append(f"node {node.id} references missing evidence")
        if node.pages is not None:
            if node.pages.end < node.pages.start:
                errors.append(f"node {node.id} has a reversed page range")
            if not node.pages.start <= node.position.physical_page <= node.pages.end:
                errors.append(f"node {node.id} position falls outside its page range")
            if any(not node.pages.start <= locator.physical_page <= node.pages.end for locator in node.page_locators):
                errors.append(f"node {node.id} has a locator outside its page range")
        elif node.page_locators:
            errors.append(f"node {node.id} has page locators without a page range")
        positions = [(by_id[child_id].position.physical_page, by_id[child_id].position.reading_order)
                     for child_id in node.child_ids if child_id in by_id]
        if positions != sorted(positions):
            errors.append(f"node {node.id} children are not in canonical reading order")

    for evidence in tree.evidence:
        if evidence.locator.physical_page < 1:
            errors.append(f"evidence {evidence.id} has an invalid physical page")
    try:
        reached = set(descendants(tree, tree.root_id, include_self=True))
        if reached != set(by_id):
            errors.append("all nodes must be reachable from the root")
    except StructureValidationError as exc:
        errors.append(str(exc))
    if tree.quality.node_count != len(tree.nodes):
        errors.append("quality.node_count does not match nodes")
    if tree.quality.owned_block_count != sum(block_owners.values()):
        errors.append("quality.owned_block_count does not match owned source blocks")
    if tree.quality.source_block_count < sum(block_owners.values()):
        errors.append("owned source block count exceeds total source block count")
    return errors


def require_valid_book_map(tree: BookMapLike) -> None:
    errors = validate_book_map(tree)
    if errors:
        raise StructureValidationError("; ".join(errors))
