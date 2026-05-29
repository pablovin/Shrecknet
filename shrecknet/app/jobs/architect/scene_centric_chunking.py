from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Any

from app.core.config_store import LLMModelTarget
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.jobs.shrecknet import validate_or_repair_json
from app.jobs.architect import prompts as architect_prompts


logger = logging.getLogger(__name__)


PARAGRAPH_MARKER_PATTERN = re.compile(r"^\[P(\d+)\]\s*(.*)$")
_BULLET_OR_NUMBERED_START = re.compile(r"^\s*(?:[●•\-\*]\s+|\d+[.)]\s+)")
_TIMESTAMP_MARKER = re.compile(r"\(\d{1,2}:\d{2}:\d{2}\)")
PARAGRAPH_CHUNK_SIZE = 30
MIN_HEADING_PARAGRAPHS = 5
MAX_PARAGRAPHS_PER_CHUNK = 50_000


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
        f"[P{position}] {paragraph}"
        for position, paragraph in enumerate(paragraphs, start=paragraph_start)
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


def _split_oversized_chunk_by_paragraph_count(
    chunk: SceneChunk,
    *,
    max_paragraphs_per_chunk: int,
    encoding_name: str,
) -> list[SceneChunk]:
    if chunk.paragraph_count <= max_paragraphs_per_chunk:
        return [chunk]

    paragraphs = list(chunk.paragraphs)
    if len(paragraphs) <= 1:
        return [chunk]

    midpoint = len(paragraphs) // 2
    left = _build_scene_chunk(
        chunk_index=0,
        paragraph_start=chunk.paragraph_start,
        paragraphs=paragraphs[:midpoint],
        encoding_name=encoding_name,
    )
    right = _build_scene_chunk(
        chunk_index=0,
        paragraph_start=chunk.paragraph_start + midpoint,
        paragraphs=paragraphs[midpoint:],
        encoding_name=encoding_name,
    )

    return (
        _split_oversized_chunk_by_paragraph_count(
            left,
            max_paragraphs_per_chunk=max_paragraphs_per_chunk,
            encoding_name=encoding_name,
        )
        + _split_oversized_chunk_by_paragraph_count(
            right,
            max_paragraphs_per_chunk=max_paragraphs_per_chunk,
            encoding_name=encoding_name,
        )
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

    normalized_chunks: list[SceneChunk] = []
    for chunk in chunks:
        normalized_chunks.extend(
            _split_oversized_chunk_by_paragraph_count(
                chunk,
                max_paragraphs_per_chunk=MAX_PARAGRAPHS_PER_CHUNK,
                encoding_name=encoding_name,
            )
        )

    reindexed_chunks: list[SceneChunk] = []
    for idx, chunk in enumerate(normalized_chunks):
        reindexed_chunks.append(
            SceneChunk(
                chunk_index=idx,
                paragraph_start=chunk.paragraph_start,
                paragraph_end=chunk.paragraph_end,
                paragraph_count=chunk.paragraph_count,
                token_count=chunk.token_count,
                paragraphs=list(chunk.paragraphs),
                marked_paragraphs=chunk.marked_paragraphs,
            )
        )

    return reindexed_chunks


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
            marker = f"[P{start_idx + len(current) + 1}] {paragraphs[index]}"
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
            f"[P{position}] {paragraph}"
            for position, paragraph in enumerate(current, start=start_idx + 1)
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


def _parse_scene_payload_with_repair(response_text: str) -> dict[str, Any]:
    """Parse model payload, attempting lightweight JSON repairs on malformed output."""
    candidate = _extract_json_object(response_text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        repaired = candidate
        # Normalize common model formatting artifacts.
        repaired = repaired.replace("“", "\"").replace("”", "\"").replace("’", "'")
        # Remove trailing commas before closing object/array.
        repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
        # Quote bare keys: { scenes: [...] } -> {"scenes":[...]}
        repaired = re.sub(r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)', r'\1"\2"\3', repaired)
        return json.loads(repaired)


async def _retry_scene_payload_via_llm_json_repair(
    *,
    llm_client: ShreckLLMClient,
    model: str | LLMModelTarget,
    malformed_text: str,
) -> dict[str, Any]:
    parsed = await validate_or_repair_json(
        llm_client=llm_client,
        model=model,
        raw_text=malformed_text,
        schema_hint='{"scenes":[{"scene_id":0,"name":"...","description":"...","start_paragraph":1,"end_paragraph":4}]}',
        usage_tag="architect.scene_discovery.json_repair",
    )
    if isinstance(parsed, dict):
        return parsed
    return {}


def attach_scene_text(
    scenes: list[dict[str, Any]],
    paragraphs: list[str],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for scene in scenes:
        start = int(scene.get("start_paragraph") or 1)
        end = int(scene.get("end_paragraph") or start)
        clipped_start = max(1, min(start, len(paragraphs)))
        clipped_end = max(clipped_start, min(end, len(paragraphs)))
        marked_lines = [
            f"[P{idx}] {paragraphs[idx - 1]}"
            for idx in range(clipped_start, clipped_end + 1)
        ]
        enriched.append({**scene, "text": "\n".join(marked_lines)})
    return enriched


def _normalize_text_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _parse_marked_paragraphs(marked_paragraphs: str) -> tuple[list[int], dict[int, str]]:
    ids: list[int] = []
    by_id: dict[int, str] = {}
    for line in str(marked_paragraphs or "").splitlines():
        match = PARAGRAPH_MARKER_PATTERN.match(line.strip())
        if not match:
            continue
        pid = int(match.group(1))
        ids.append(pid)
        by_id[pid] = match.group(2).strip()
    return sorted(set(ids)), by_id


def _normalize_scene_ranges_global(
    scenes: list[dict[str, Any]],
    allowed_ids: list[int],
) -> list[dict[str, Any]]:
    if not scenes:
        raise ValueError("Model returned no scenes")
    if not allowed_ids:
        raise ValueError("No allowed paragraph ids found")

    min_id = min(allowed_ids)
    max_id = max(allowed_ids)

    def _parse_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    normalized: list[dict[str, Any]] = []
    for idx, scene in enumerate(scenes):
        raw_start = _parse_int(scene.get("start_paragraph"))
        raw_end = _parse_int(scene.get("end_paragraph"))
        start_value = raw_start if raw_start is not None else min_id
        end_value = raw_end if raw_end is not None else start_value
        if start_value > end_value:
            start_value, end_value = end_value, start_value

        clipped_start = min(max(start_value, min_id), max_id)
        clipped_end = min(max(end_value, min_id), max_id)
        span_ids = [pid for pid in allowed_ids if clipped_start <= pid <= clipped_end]
        if not span_ids:
            nearest = min(allowed_ids, key=lambda pid: abs(pid - clipped_start))
            span_ids = [nearest]

        normalized.append(
            {
                "scene_id": int(scene.get("scene_id", len(normalized))),
                "raw_scene_order": idx,
                "name": str(scene.get("name", "")).strip(),
                "description": str(scene.get("description", "")).strip(),
                "start_paragraph": span_ids[0],
                "end_paragraph": span_ids[-1],
                "source_paragraphs_absolute": span_ids,
                "raw_start_paragraph": start_value,
                "raw_end_paragraph": end_value,
            }
        )
    return normalized



async def segment_chunk_into_scenes(
    *,
    llm_client: ShreckLLMClient,
    model: str | LLMModelTarget,
    repair_model: str | LLMModelTarget,
    marked_paragraphs: str,
    paragraph_count: int,
    paragraphs: list[str],
    instructions: str | None = None,
    debug_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    del paragraph_count
    if not paragraphs:
        return []
    allowed_ids, paragraph_by_id = _parse_marked_paragraphs(marked_paragraphs)
    if not allowed_ids:
        return []

    instructions_text = str(instructions or "").strip()
    prompt = architect_prompts.ARCHITECT_SCENE_SEGMENTATION_PROMPT.format(
        marked_paragraphs=marked_paragraphs
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

    used_json_repair = False
    fallback_used = False
    parse_error: str | None = None
    try:
        payload = _parse_scene_payload_with_repair(response_text)
    except Exception as exc:
        parse_error = str(exc)
        logger.warning("scene_chunk_parse_error_retry_json_repair: error=%s", exc)
        try:
            payload = await _retry_scene_payload_via_llm_json_repair(
                llm_client=llm_client,
                model=repair_model,
                malformed_text=response_text,
            )
            used_json_repair = True
        except Exception as retry_exc:
            logger.warning(
                "scene_chunk_parse_error_fallback_single_scene: initial_error=%s retry_error=%s",
                exc,
                retry_exc,
            )
            fallback = {
                "scene_id": 0,
                "raw_scene_order": 0,
                "name": "Scene",
                "description": "",
                "start_paragraph": allowed_ids[0],
                "end_paragraph": allowed_ids[-1],
                "source_paragraphs_absolute": list(allowed_ids),
                "raw_start_paragraph": allowed_ids[0],
                "raw_end_paragraph": allowed_ids[-1],
                "text": "\n".join(f"[P{pid}] {paragraph_by_id.get(pid, '')}" for pid in allowed_ids),
            }
            fallback_used = True
            normalized = [fallback]
            if debug_rows is not None:
                debug_rows.append(
                    {
                        "paragraph_count": len(allowed_ids),
                        "marked_paragraphs": marked_paragraphs,
                        "raw_llm_response": str(response_text),
                        "parsed_payload": None,
                        "normalized_scenes": normalized,
                        "used_json_repair": used_json_repair,
                        "fallback_used": fallback_used,
                        "parse_error": parse_error,
                        "retry_error": str(retry_exc),
                    }
                )
            return normalized
    raw_scenes = payload.get("scenes")
    if not isinstance(raw_scenes, list):
        logger.warning("scene_chunk_invalid_payload: keys=%s", list(payload.keys()))
        raw_scenes = []

    scene_items = [item for item in raw_scenes if isinstance(item, dict)]
    if not scene_items:
        fallback = {
            "scene_id": 0,
            "name": "Scene",
            "description": "",
            "start_paragraph": allowed_ids[0],
            "end_paragraph": allowed_ids[-1],
            "source_paragraphs_absolute": list(allowed_ids),
            "text": "\n".join(f"[P{pid}] {paragraph_by_id.get(pid, '')}" for pid in allowed_ids),
        }
        fallback_used = True
        normalized = [fallback]
        if debug_rows is not None:
            debug_rows.append(
                {
                    "paragraph_count": len(allowed_ids),
                    "marked_paragraphs": marked_paragraphs,
                    "raw_llm_response": str(response_text),
                    "parsed_payload": payload,
                    "normalized_scenes": normalized,
                    "used_json_repair": used_json_repair,
                    "fallback_used": fallback_used,
                    "parse_error": parse_error,
                }
            )
        return normalized

    normalized_final = _normalize_scene_ranges_global(scene_items, allowed_ids)
    for scene in normalized_final:
        scene.pop("milestones", None)
        scene.pop("mentions", None)
        scene.pop("related_to", None)
        span_ids = list(scene.get("source_paragraphs_absolute") or [])
        scene["text"] = "\n".join(
            f"[P{pid}] {paragraph_by_id.get(pid, '')}"
            for pid in span_ids
            if pid in paragraph_by_id
        )
    normalized = normalized_final
    if debug_rows is not None:
        debug_rows.append(
            {
                "paragraph_count": len(allowed_ids),
                "marked_paragraphs": marked_paragraphs,
                "raw_llm_response": str(response_text),
                "parsed_payload": payload,
                "normalized_scenes": normalized,
                "used_json_repair": used_json_repair,
                "fallback_used": fallback_used,
                "parse_error": parse_error,
            }
        )
    return normalized
