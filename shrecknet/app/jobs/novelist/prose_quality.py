"""Local completion checks and safe HTML rendering for Novelist v4 prose."""

from __future__ import annotations

import html
import re


_LIST_LINE = re.compile(r"^\s*(?:[-*•]\s+|\d+[.)]\s+)", re.MULTILINE)
_HEADING_OR_FENCE = re.compile(r"^\s*(?:#{1,6}\s+|```)", re.MULTILINE)
_HTML_TAG = re.compile(r"<\/?[A-Za-z][^>]*>")


def validate_prose_text(
    text: str, *, target_words: int, finish_reason: str | None
) -> list[str]:
    """Return only failures severe enough to justify one generation retry."""

    errors: list[str] = []
    stripped = text.strip()
    if not stripped:
        return ["empty response"]
    normalized_finish = str(finish_reason or "").casefold()
    if normalized_finish in {"length", "max_tokens", "max_output_tokens"}:
        errors.append("generation was truncated at the output-token limit")
    word_count = len(stripped.split())
    minimum_words = max(180, int(target_words * 0.35))
    if word_count < minimum_words:
        errors.append(
            f"response is unexpectedly short ({word_count} words; minimum {minimum_words})"
        )
    if len(_LIST_LINE.findall(stripped)) >= 2:
        errors.append("response is formatted as a list")
    if _HEADING_OR_FENCE.search(stripped):
        errors.append("response contains a heading or Markdown fence")
    if _HTML_TAG.search(stripped):
        errors.append("response contains HTML instead of plain prose")
    return errors


def prose_to_html(text: str) -> str:
    """Escape accepted plain prose and wrap each paragraph in backend-owned HTML."""

    paragraphs = [
        part.strip() for part in re.split(r"\n\s*\n", text.strip()) if part.strip()
    ]
    normalized = [re.sub(r"\s*\n\s*", " ", paragraph) for paragraph in paragraphs]
    return "\n".join(
        f"<p>{html.escape(paragraph, quote=False)}</p>" for paragraph in normalized
    )
