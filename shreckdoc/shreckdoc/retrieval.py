"""Librarian's storage-independent named-term extraction and reciprocal rank fusion."""
from __future__ import annotations
import re
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any
RRF_K = 60

def extract_named_terms(query: str) -> list[str]:
    quoted = re.findall(r'["“]([^"”]{2,80})["”]', query)
    titled = re.findall(
        r"\b[A-Z][A-Za-z0-9'\-]*(?:\s+[A-Z][A-Za-z0-9'\-]*){0,3}\b", query
    )
    stop = {"What", "Which", "When", "Where", "How", "Give", "Explain", "Please", "Can", "Could", "The"}
    values: list[str] = []
    seen: set[str] = set()
    for value in quoted + titled:
        value = re.sub(r"\s+", " ", value).strip()
        if not value or value.split()[0] in stop or value.casefold() in seen:
            continue
        seen.add(value.casefold())
        values.append(value)
    return values[:8]


def reciprocal_rank_fusion(branches: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for branch, rows in branches.items():
        seen: set[str] = set()
        for rank, row in enumerate(rows, 1):
            props = dict(row.get("props") or {})
            chunk_id = str(props.get("chunk_id") or f"{props.get('library_item_id')}:{props.get('chunk_index')}")
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            candidate = merged.setdefault(chunk_id, {"props": props, "chunk_id": chunk_id, "rrf_score": 0.0, "branch_ranks": {}})
            candidate["rrf_score"] += 1.0 / (RRF_K + rank)
            candidate["branch_ranks"][branch] = rank
    return sorted(merged.values(), key=lambda item: (-item["rrf_score"], item["chunk_id"]))


def select_diverse(candidates: list[dict[str, Any]], target: int) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    selected: list[dict[str, Any]] = []
    exclusions: list[dict[str, str]] = []
    parent_counts: defaultdict[str, int] = defaultdict(int)
    item_counts: defaultdict[str, int] = defaultdict(int)
    for candidate in candidates:
        props = candidate["props"]
        parent = str(props.get("parent_chunk_id") or candidate["chunk_id"])
        item = str(props.get("library_item_id") or "")
        text = re.sub(r"\s+", " ", str(props.get("display_text") or "")).casefold()
        if any(SequenceMatcher(None, text[:2000], old["_text"][:2000]).ratio() >= 0.92 for old in selected):
            exclusions.append({"chunk_id": candidate["chunk_id"], "reason": "near_duplicate"})
            continue
        if parent_counts[parent] >= 1 or (item_counts[item] >= 2 and len({str(x["props"].get("library_item_id") or "") for x in candidates}) > 1):
            exclusions.append({"chunk_id": candidate["chunk_id"], "reason": "diversity_cap"})
            continue
        candidate["_text"] = text
        selected.append(candidate)
        parent_counts[parent] += 1
        item_counts[item] += 1
        if len(selected) >= target:
            break
    if len(selected) < target:
        selected_ids = {x["chunk_id"] for x in selected}
        for candidate in candidates:
            if candidate["chunk_id"] not in selected_ids:
                candidate["_text"] = re.sub(r"\s+", " ", str(candidate["props"].get("display_text") or "")).casefold()
                selected.append(candidate)
                selected_ids.add(candidate["chunk_id"])
                if len(selected) >= target:
                    break
    for candidate in selected:
        candidate.pop("_text", None)
    return selected, exclusions
