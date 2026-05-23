from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from html import unescape
from html.parser import HTMLParser
from typing import Any

from app.core.config_store import LLMModelTarget
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.jobs.architect import prompts as architect_prompts


logger = logging.getLogger(__name__)


PARAGRAPH_MARKER_PATTERN = re.compile(r"^\[P(\d+)\]\s*(.*)$")
_BULLET_OR_NUMBERED_START = re.compile(r"^\s*(?:[●•\-\*]\s+|\d+[.)]\s+)")
_TIMESTAMP_MARKER = re.compile(r"\(\d{1,2}:\d{2}:\d{2}\)")
PARAGRAPH_CHUNK_SIZE = 30
MIN_HEADING_PARAGRAPHS = 5
LOCAL_SCENE_BUNDLE_SIZE = 12
LOCAL_SCENE_BUNDLE_OVERLAP = 2
MAX_CANDIDATE_SCENES_PER_BUNDLE = 3


@dataclass
class SceneChunk:
    chunk_index: int
    paragraph_start: int
    paragraph_end: int
    paragraph_count: int
    token_count: int
    paragraphs: list[str]
    marked_paragraphs: str


class _NarrativeHTMLParagraphParser(HTMLParser):
    """Extract paragraph-like units and preserve heading -> paragraph grouping."""

    _PARAGRAPH_TAGS = {"p", "li", "blockquote"}
    _HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "title"}

    def __init__(self) -> None:
        super().__init__()
        self._tag_stack: list[str] = []
        self._paragraph_buffer: list[str] = []
        self._pending_non_paragraph: list[str] = []
        self._paragraphs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        self._tag_stack.append(tag.lower())

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if self._tag_stack:
            for idx in range(len(self._tag_stack) - 1, -1, -1):
                if self._tag_stack[idx] == normalized:
                    del self._tag_stack[idx]
                    break

        if normalized in self._PARAGRAPH_TAGS:
            self._flush_paragraph()
            return

        if normalized in self._HEADING_TAGS:
            self._flush_heading_to_pending()

    def handle_data(self, data: str) -> None:
        cleaned = " ".join(unescape(data).split())
        if not cleaned:
            return

        current_tag = self._tag_stack[-1] if self._tag_stack else ""
        if current_tag in self._PARAGRAPH_TAGS:
            self._paragraph_buffer.append(cleaned)
            return

        if current_tag in self._HEADING_TAGS:
            self._pending_non_paragraph.append(cleaned)
            return

        self._pending_non_paragraph.append(cleaned)

    def close(self) -> None:
        super().close()
        self._flush_paragraph()
        if self._pending_non_paragraph:
            self._paragraphs.append(" ".join(self._pending_non_paragraph))
            self._pending_non_paragraph = []

    @property
    def paragraphs(self) -> list[str]:
        return [p for p in self._paragraphs if p]

    def _flush_heading_to_pending(self) -> None:
        if self._paragraph_buffer:
            self._pending_non_paragraph.append(" ".join(self._paragraph_buffer))
            self._paragraph_buffer = []

    def _flush_paragraph(self) -> None:
        if not self._paragraph_buffer:
            return

        paragraph_text = " ".join(self._paragraph_buffer)
        self._paragraph_buffer = []

        if self._pending_non_paragraph:
            paragraph_text = " ".join(self._pending_non_paragraph + [paragraph_text])
            self._pending_non_paragraph = []

        self._paragraphs.append(paragraph_text)


class _NarrativeHTMLHeadingSectionParser(HTMLParser):
    """Extract heading-scoped paragraph sections from HTML content."""

    _PARAGRAPH_TAGS = {"p", "li", "blockquote"}
    _HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__()
        self._tag_stack: list[str] = []
        self._paragraph_buffer: list[str] = []
        self._heading_buffer: list[str] = []
        self._current_heading: str | None = None
        self._current_paragraphs: list[str] = []
        self._sections: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        self._tag_stack.append(tag.lower())

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if self._tag_stack:
            for idx in range(len(self._tag_stack) - 1, -1, -1):
                if self._tag_stack[idx] == normalized:
                    del self._tag_stack[idx]
                    break

        if normalized in self._PARAGRAPH_TAGS:
            self._flush_paragraph()
            return

        if normalized in self._HEADING_TAGS:
            heading_text = " ".join(self._heading_buffer).strip()
            self._heading_buffer = []
            self._start_new_section(heading_text or None)

    def handle_data(self, data: str) -> None:
        cleaned = " ".join(unescape(data).split())
        if not cleaned:
            return

        current_tag = self._tag_stack[-1] if self._tag_stack else ""
        if current_tag in self._PARAGRAPH_TAGS:
            self._paragraph_buffer.append(cleaned)
            return
        if current_tag in self._HEADING_TAGS:
            self._heading_buffer.append(cleaned)

    def close(self) -> None:
        super().close()
        self._flush_paragraph()
        self._flush_section()

    @property
    def sections(self) -> list[dict[str, Any]]:
        return [
            {
                "heading": section.get("heading"),
                "paragraphs": [p for p in section.get("paragraphs", []) if p],
            }
            for section in self._sections
            if section.get("paragraphs")
        ]

    def _flush_paragraph(self) -> None:
        if not self._paragraph_buffer:
            return
        paragraph_text = " ".join(self._paragraph_buffer)
        self._paragraph_buffer = []
        if paragraph_text:
            self._current_paragraphs.append(paragraph_text)

    def _flush_section(self) -> None:
        if not self._current_paragraphs:
            self._current_heading = None
            return
        self._sections.append(
            {
                "heading": self._current_heading,
                "paragraphs": list(self._current_paragraphs),
            }
        )
        self._current_paragraphs = []
        self._current_heading = None

    def _start_new_section(self, heading: str | None) -> None:
        self._flush_paragraph()
        self._flush_section()
        self._current_heading = heading


def _looks_like_html(value: str) -> bool:
    return bool(re.search(r"<[^>]+>", value))


def _extract_non_html_paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n+", text)
    normalized_parts = [
        " ".join(unescape(part).split()) for part in parts if part and part.strip()
    ]
    if len(normalized_parts) > 1:
        return normalized_parts

    # Fallback for transcript-like inputs that use single newlines with list markers
    # rather than blank lines between semantic blocks.
    lines = [line.strip() for line in text.splitlines() if line and line.strip()]
    if not lines:
        return []

    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        starts_block = bool(_BULLET_OR_NUMBERED_START.match(line)) or bool(
            _TIMESTAMP_MARKER.search(line)
        )
        if starts_block and current:
            blocks.append(current)
            current = [line]
            continue
        current.append(line)

    if current:
        blocks.append(current)

    if len(blocks) <= 1:
        return normalized_parts

    return [" ".join(unescape(" ".join(block)).split()) for block in blocks if block]


def extract_paragraphs_from_sources(
    text: str | None,
    autogenerated_text: str | None,
) -> list[str]:
    paragraphs: list[str] = []

    for source in (text, autogenerated_text):
        if not source:
            continue

        if _looks_like_html(source):
            parser = _NarrativeHTMLParagraphParser()
            parser.feed(source)
            parser.close()
            paragraphs.extend(parser.paragraphs)
        else:
            paragraphs.extend(_extract_non_html_paragraphs(source))

    return [p for p in paragraphs if p]


def _extract_heading_sections_from_html(source: str) -> list[list[str]]:
    parser = _NarrativeHTMLHeadingSectionParser()
    parser.feed(source)
    parser.close()
    sections = parser.sections
    return [section.get("paragraphs", []) for section in sections if section.get("paragraphs")]


def _count_tokens_for_marked_text(marked_paragraphs: str, encoding_name: str = "cl100k_base") -> int:
    try:
        import tiktoken  # type: ignore

        encoder = tiktoken.get_encoding(encoding_name)
        return len(encoder.encode(marked_paragraphs))
    except Exception:
        return len(marked_paragraphs.split())


def _build_scene_chunk(
    *,
    chunk_index: int,
    paragraph_start: int,
    paragraphs: list[str],
    encoding_name: str,
) -> SceneChunk:
    marked = "\n".join(
        f"[P{position}] {paragraph}" for position, paragraph in enumerate(paragraphs, start=1)
    )
    return SceneChunk(
        chunk_index=chunk_index,
        paragraph_start=paragraph_start,
        paragraph_end=paragraph_start + len(paragraphs) - 1,
        paragraph_count=len(paragraphs),
        token_count=_count_tokens_for_marked_text(marked, encoding_name=encoding_name),
        paragraphs=paragraphs,
        marked_paragraphs=marked,
    )


def build_scene_chunks_from_sources(
    text: str | None,
    autogenerated_text: str | None,
    *,
    encoding_name: str = "cl100k_base",
) -> list[SceneChunk]:
    """Build chunk windows using heading-aware sections or paragraph-size fallback."""
    section_entries: list[dict[str, Any]] = []

    for source in (text, autogenerated_text):
        if not source:
            continue

        if _looks_like_html(source):
            heading_sections = _extract_heading_sections_from_html(source)
            if heading_sections:
                for section in heading_sections:
                    normalized = [p for p in section if p]
                    if normalized:
                        section_entries.append({"is_heading": True, "paragraphs": normalized})
                continue

        paragraphs = _extract_non_html_paragraphs(source)
        if paragraphs:
            section_entries.append({"is_heading": False, "paragraphs": paragraphs})

    if not section_entries:
        return []

    chunks: list[SceneChunk] = []
    chunk_index = 0
    paragraph_cursor = 1
    idx = 0

    while idx < len(section_entries):
        entry = section_entries[idx]
        is_heading = bool(entry.get("is_heading"))
        paragraphs = [p for p in entry.get("paragraphs", []) if p]
        if not paragraphs:
            idx += 1
            continue

        if not is_heading:
            start = 0
            while start < len(paragraphs):
                end = min(len(paragraphs), start + PARAGRAPH_CHUNK_SIZE)
                current = paragraphs[start:end]
                chunks.append(
                    _build_scene_chunk(
                        chunk_index=chunk_index,
                        paragraph_start=paragraph_cursor,
                        paragraphs=current,
                        encoding_name=encoding_name,
                    )
                )
                chunk_index += 1
                paragraph_cursor += len(current)
                start = end
            idx += 1
            continue

        merged = list(paragraphs)
        consume_until = idx
        if len(merged) < MIN_HEADING_PARAGRAPHS:
            next_idx = idx + 1
            while next_idx < len(section_entries) and len(merged) < PARAGRAPH_CHUNK_SIZE:
                next_entry = section_entries[next_idx]
                if not bool(next_entry.get("is_heading")):
                    break
                next_paragraphs = [p for p in next_entry.get("paragraphs", []) if p]
                merged.extend(next_paragraphs)
                consume_until = next_idx
                next_idx += 1

        chunks.append(
            _build_scene_chunk(
                chunk_index=chunk_index,
                paragraph_start=paragraph_cursor,
                paragraphs=merged,
                encoding_name=encoding_name,
            )
        )
        chunk_index += 1
        paragraph_cursor += len(merged)
        idx = consume_until + 1

    return chunks


def build_scene_chunks(
    paragraphs: list[str],
    *,
    token_limit: int,
    encoding_name: str = "cl100k_base",
) -> list[SceneChunk]:
    if not paragraphs:
        return []

    try:
        import tiktoken  # type: ignore
    except ImportError as exc:  # pragma: no cover - dependency/runtime guard
        raise RuntimeError(
            "tiktoken is required for scene-centric chunking. Install project dependencies."
        ) from exc

    encoder = tiktoken.get_encoding(encoding_name)

    chunks: list[SceneChunk] = []
    chunk_index = 0
    index = 0
    paragraph_total = len(paragraphs)

    while index < paragraph_total:
        current: list[str] = []
        current_tokens = 0
        start_idx = index

        while index < paragraph_total:
            marker = f"[P{len(current) + 1}] {paragraphs[index]}"
            marker_tokens = len(encoder.encode(marker))
            if current and current_tokens + marker_tokens > token_limit:
                break
            if not current and marker_tokens > token_limit:
                # Keep forward progress even for oversized paragraphs.
                current.append(paragraphs[index])
                current_tokens = marker_tokens
                index += 1
                break

            current.append(paragraphs[index])
            current_tokens += marker_tokens
            index += 1

        marked = "\n".join(
            f"[P{position}] {paragraph}" for position, paragraph in enumerate(current, start=1)
        )
        chunks.append(
            SceneChunk(
                chunk_index=chunk_index,
                paragraph_start=start_idx + 1,
                paragraph_end=start_idx + len(current),
                paragraph_count=len(current),
                token_count=current_tokens,
                paragraphs=current,
                marked_paragraphs=marked,
            )
        )
        chunk_index += 1

    return chunks


def _extract_json_object(raw: str) -> str:
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in model response")
    return raw[start : end + 1]


def _normalize_scene_ranges(
    scenes: list[dict[str, Any]],
    paragraph_count: int,
    *,
    strict: bool = True,
) -> list[dict[str, Any]]:
    if not scenes:
        raise ValueError("Model returned no scenes")

    # Accept model output as either zero-based or one-based indices.
    starts = [int(scene.get("start_paragraph", -1)) for scene in scenes]
    ends = [int(scene.get("end_paragraph", -1)) for scene in scenes]
    zero_based = any(value == 0 for value in starts + ends)

    normalized: list[dict[str, Any]] = []
    for scene in scenes:
        start = int(scene.get("start_paragraph", -1))
        end = int(scene.get("end_paragraph", -1))
        if zero_based:
            start += 1
            end += 1
        if strict and (start < 1 or end < 1 or start > end or end > paragraph_count):
            raise ValueError(
                f"Invalid scene range start={start} end={end} for paragraph_count={paragraph_count}"
            )

        # In tolerant mode we clamp and repair invalid model output.
        if not strict:
            start = max(1, min(start, paragraph_count))
            end = max(1, min(end, paragraph_count))
            if end < start:
                end = start

        normalized.append(
            {
                "scene_id": int(scene.get("scene_id", len(normalized))),
                "name": str(scene.get("name", "")).strip(),
                "description": str(scene.get("description", "")).strip(),
                "start_paragraph": start,
                "end_paragraph": end,
            }
        )

    normalized.sort(key=lambda item: (item["start_paragraph"], item["end_paragraph"]))

    if not strict:
        repaired: list[dict[str, Any]] = []
        expected = 1
        total = len(normalized)

        for idx, item in enumerate(normalized):
            if expected > paragraph_count:
                break

            start = expected
            raw_end = item["end_paragraph"]
            end = max(start, min(raw_end, paragraph_count))

            # Keep at least one paragraph available for remaining scenes.
            remaining = total - idx - 1
            max_end_for_item = max(start, paragraph_count - remaining)
            if end > max_end_for_item:
                end = max_end_for_item

            repaired.append(
                {
                    **item,
                    "start_paragraph": start,
                    "end_paragraph": end,
                }
            )
            expected = end + 1

        if not repaired:
            repaired = [
                {
                    "scene_id": 0,
                    "name": "Scene 0",
                    "description": "",
                    "start_paragraph": 1,
                    "end_paragraph": paragraph_count,
                }
            ]
        elif repaired[-1]["end_paragraph"] < paragraph_count:
            repaired[-1]["end_paragraph"] = paragraph_count

        if repaired != normalized:
            logger.warning(
                "scene_range_repair_applied: scene_count=%d paragraph_count=%d",
                len(normalized),
                paragraph_count,
            )
        return repaired

    expected = 1
    for item in normalized:
        if item["start_paragraph"] != expected:
            raise ValueError(
                f"Scene coverage gap/overlap near paragraph {expected}; got start={item['start_paragraph']}"
            )
        expected = item["end_paragraph"] + 1

    if expected != paragraph_count + 1:
        raise ValueError(
            f"Scene coverage incomplete, expected paragraph {paragraph_count}, got up to {expected - 1}"
        )

    return normalized


def attach_scene_text(
    scenes: list[dict[str, Any]],
    paragraphs: list[str],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for scene in scenes:
        start = scene["start_paragraph"]
        end = scene["end_paragraph"]
        marked_lines = [
            f"[P{idx}] {paragraphs[idx - 1]}" for idx in range(start, end + 1)
        ]
        enriched.append({**scene, "text": "\n".join(marked_lines)})
    return enriched


def _normalize_text_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _milestone_signature(item: dict[str, Any]) -> str:
    title = _normalize_text_key(item.get("title") or item.get("label"))
    description = _normalize_text_key(item.get("description"))
    if not title and not description:
        return ""
    return f"{title}|{description}"


def _scene_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_text = f"{left.get('name', '')} {left.get('description', '')}".strip()
    right_text = f"{right.get('name', '')} {right.get('description', '')}".strip()
    if not left_text and not right_text:
        return 0.0
    return SequenceMatcher(
        None,
        _normalize_text_key(left_text),
        _normalize_text_key(right_text),
    ).ratio()


def _milestone_jaccard(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_set = {
        _milestone_signature(item)
        for item in (left.get("milestones") or [])
        if isinstance(item, dict) and _milestone_signature(item)
    }
    right_set = {
        _milestone_signature(item)
        for item in (right.get("milestones") or [])
        if isinstance(item, dict) and _milestone_signature(item)
    }
    if not left_set or not right_set:
        return 0.0
    intersection = len(left_set & right_set)
    union = len(left_set | right_set)
    if union <= 0:
        return 0.0
    return intersection / union


def _ranges_overlap_strongly(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_start = int(left.get("start_paragraph") or 0)
    left_end = int(left.get("end_paragraph") or 0)
    right_start = int(right.get("start_paragraph") or 0)
    right_end = int(right.get("end_paragraph") or 0)

    overlap = max(0, min(left_end, right_end) - max(left_start, right_start) + 1)
    left_len = max(1, left_end - left_start + 1)
    right_len = max(1, right_end - right_start + 1)
    min_len = min(left_len, right_len)
    return overlap / min_len >= 0.6


def _should_merge_scenes(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if _ranges_overlap_strongly(left, right):
        return True
    if _scene_similarity(left, right) >= 0.85:
        return True
    return _milestone_jaccard(left, right) >= 0.5


def _dedupe_milestones(raw_milestones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_milestones:
        signature = _milestone_signature(item)
        if not signature or signature in seen:
            continue
        seen.add(signature)

        mentions = item.get("mentions") if isinstance(item.get("mentions"), list) else []
        mentions = sorted({str(value).strip() for value in mentions if str(value).strip()})

        related_to = item.get("related_to") if isinstance(item.get("related_to"), list) else []
        related_to_dedup: list[dict[str, Any]] = []
        related_seen: set[tuple[str, str]] = set()
        for rel in related_to:
            if not isinstance(rel, dict):
                continue
            entity = str(rel.get("entity") or "").strip()
            label = str(rel.get("relationship_label") or "related").strip().lower()
            key = (_normalize_text_key(entity), label)
            if not key[0] or key in related_seen:
                continue
            related_seen.add(key)
            related_to_dedup.append(
                {
                    "entity": entity,
                    "relationship_label": label,
                    "relationship_description": str(
                        rel.get("relationship_description") or ""
                    ).strip(),
                }
            )

        deduped.append(
            {
                "title": str(item.get("title") or item.get("label") or "").strip(),
                "description": str(item.get("description") or "").strip(),
                "boundary_type": str(item.get("boundary_type") or "none").strip().lower(),
                "mentions": mentions,
                "related_to": related_to_dedup,
            }
        )
    return deduped


def _merge_scene_pair(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    merged["start_paragraph"] = min(
        int(base.get("start_paragraph") or 1),
        int(incoming.get("start_paragraph") or 1),
    )
    merged["end_paragraph"] = max(
        int(base.get("end_paragraph") or 1),
        int(incoming.get("end_paragraph") or 1),
    )

    if len(str(incoming.get("description") or "")) > len(str(base.get("description") or "")):
        merged["description"] = str(incoming.get("description") or "").strip()
    if len(str(incoming.get("name") or "")) > len(str(base.get("name") or "")):
        merged["name"] = str(incoming.get("name") or "").strip()

    merged_milestones = [
        item
        for item in (base.get("milestones") or [])
        if isinstance(item, dict)
    ] + [
        item
        for item in (incoming.get("milestones") or [])
        if isinstance(item, dict)
    ]
    merged["milestones"] = _dedupe_milestones(merged_milestones)
    return merged


def _build_local_paragraph_bundles(paragraphs: list[str]) -> list[dict[str, Any]]:
    if not paragraphs:
        return []

    bundles: list[dict[str, Any]] = []
    step = max(1, LOCAL_SCENE_BUNDLE_SIZE - LOCAL_SCENE_BUNDLE_OVERLAP)
    start = 0
    bundle_index = 0
    total = len(paragraphs)

    while start < total:
        end = min(total, start + LOCAL_SCENE_BUNDLE_SIZE)
        local_paragraphs = paragraphs[start:end]
        marked = "\n".join(
            f"[P{idx}] {paragraph}"
            for idx, paragraph in enumerate(local_paragraphs, start=1)
        )
        bundles.append(
            {
                "bundle_index": bundle_index,
                "start_offset": start,
                "paragraphs": local_paragraphs,
                "marked_paragraphs": marked,
            }
        )
        if end >= total:
            break
        start += step
        bundle_index += 1

    return bundles


def _normalize_candidate_scene_ranges(
    candidate_scenes: list[dict[str, Any]],
    paragraph_count: int,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for idx, scene in enumerate(candidate_scenes):
        start = int(scene.get("start_paragraph", idx + 1))
        end = int(scene.get("end_paragraph", start))
        start = max(1, min(start, paragraph_count))
        end = max(1, min(end, paragraph_count))
        if end < start:
            end = start
        normalized.append(
            {
                "scene_id": int(scene.get("scene_id", idx)),
                "name": str(scene.get("name", "")).strip() or f"Candidate {idx}",
                "description": str(scene.get("description", "")).strip(),
                "start_paragraph": start,
                "end_paragraph": end,
                "milestones": (
                    scene.get("milestones")
                    if isinstance(scene.get("milestones"), list)
                    else []
                ),
            }
        )
    normalized.sort(key=lambda item: (item["start_paragraph"], item["end_paragraph"]))
    return normalized


def _merge_candidate_scenes(candidate_scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for candidate in sorted(
        candidate_scenes,
        key=lambda item: (item.get("start_paragraph", 0), item.get("end_paragraph", 0)),
    ):
        matched_index: int | None = None
        for idx, current in enumerate(merged):
            if _should_merge_scenes(current, candidate):
                matched_index = idx
                break
        if matched_index is None:
            merged.append(
                {
                    **candidate,
                    "milestones": _dedupe_milestones(
                        [item for item in (candidate.get("milestones") or []) if isinstance(item, dict)]
                    ),
                }
            )
        else:
            merged[matched_index] = _merge_scene_pair(merged[matched_index], candidate)

    for idx, scene in enumerate(merged):
        scene["scene_id"] = idx

    merged.sort(key=lambda item: (item["start_paragraph"], item["end_paragraph"]))
    return merged


async def segment_chunk_into_scenes(
    *,
    llm_client: ShreckLLMClient,
    model: str | LLMModelTarget,
    marked_paragraphs: str,
    paragraph_count: int,
    paragraphs: list[str],
    instructions: str | None = None,
) -> list[dict[str, Any]]:
    del marked_paragraphs
    if paragraph_count <= 0 or not paragraphs:
        return []

    instructions_text = str(instructions or "").strip()
    bundles = _build_local_paragraph_bundles(paragraphs)
    candidate_scenes_global: list[dict[str, Any]] = []

    for bundle in bundles:
        prompt = architect_prompts.ARCHITECT_SCENE_CENTRIC_CHUNKING_PROMPT.format(
            marked_paragraphs=bundle["marked_paragraphs"]
        )
        if instructions_text:
            prompt = (
                f"{prompt}\n\nFrontend instructions (authoritative constraints):\n"
                f"{instructions_text}"
            )

        response_text = await llm_client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            usage_tag="architect.scene_discovery",
        )

        payload = json.loads(_extract_json_object(response_text))
        bundle_candidates = payload.get("candidate_scenes")
        if not isinstance(bundle_candidates, list):
            bundle_candidates = payload.get("scenes")
        if not isinstance(bundle_candidates, list):
            logger.warning(
                "scene_bundle_invalid_payload: bundle_index=%s keys=%s",
                bundle.get("bundle_index"),
                list(payload.keys()),
            )
            continue

        bundle_candidates = bundle_candidates[:MAX_CANDIDATE_SCENES_PER_BUNDLE]
        normalized_bundle = _normalize_candidate_scene_ranges(
            [item for item in bundle_candidates if isinstance(item, dict)],
            len(bundle["paragraphs"]),
        )

        start_offset = int(bundle["start_offset"])
        for item in normalized_bundle:
            candidate_scenes_global.append(
                {
                    **item,
                    "start_paragraph": int(item["start_paragraph"]) + start_offset,
                    "end_paragraph": int(item["end_paragraph"]) + start_offset,
                }
            )

    if not candidate_scenes_global:
        fallback = {
            "scene_id": 0,
            "name": "Scene",
            "description": "",
            "start_paragraph": 1,
            "end_paragraph": paragraph_count,
            "milestones": [],
        }
        return attach_scene_text([fallback], paragraphs)

    merged_scenes = _merge_candidate_scenes(candidate_scenes_global)
    normalized_final = _normalize_scene_ranges(merged_scenes, paragraph_count, strict=False)

    milestone_by_signature: dict[str, list[dict[str, Any]]] = {}
    for scene in merged_scenes:
        signature = (
            int(scene.get("start_paragraph") or 0),
            int(scene.get("end_paragraph") or 0),
            _normalize_text_key(scene.get("name")),
        )
        milestone_by_signature[str(signature)] = _dedupe_milestones(
            [item for item in (scene.get("milestones") or []) if isinstance(item, dict)]
        )

    for scene in normalized_final:
        signature = (
            int(scene.get("start_paragraph") or 0),
            int(scene.get("end_paragraph") or 0),
            _normalize_text_key(scene.get("name")),
        )
        scene["milestones"] = milestone_by_signature.get(str(signature), [])

    return attach_scene_text(normalized_final, paragraphs)
