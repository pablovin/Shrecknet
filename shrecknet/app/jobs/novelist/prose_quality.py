"""Deterministic structural checks for Novelist v3 prose blocks."""

from __future__ import annotations

import re
from html.parser import HTMLParser


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)


_PARAGRAPH = re.compile(r"<p(?:\s[^>]*)?>(.*?)</p>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_LIST = re.compile(r"(^|\n)\s*(?:[-*•]\s+|\d+[.)]\s+)", re.M)


def validate_prose_html(html: str) -> list[str]:
    """Return concrete structural violations; deliberately do not score style."""
    errors: list[str] = []
    parser = _Tags()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        errors.append("malformed HTML")
    forbidden = {"ul", "ol", "li", "h1", "h2", "h3"} & set(parser.tags)
    if forbidden:
        errors.append("forbidden HTML tags: " + ", ".join(sorted(forbidden)))
    if _LIST.search(_TAG.sub("", html)):
        errors.append("markdown-style list")
    paragraphs = [re.sub(r"\s+", " ", _TAG.sub("", item)).strip() for item in _PARAGRAPH.findall(html)]
    if not paragraphs:
        errors.append("no complete <p> paragraphs")
        return errors
    if len(paragraphs) != len(set(paragraphs)):
        errors.append("repeated paragraphs")
    words = [len(paragraph.split()) for paragraph in paragraphs]
    if len(words) >= 3 and any(all(size < 18 for size in words[i : i + 3]) for i in range(len(words) - 2)):
        errors.append("three consecutive very short paragraphs")
    if len(words) >= 4:
        tail = words[max(0, int(len(words) * 0.7)) :]
        head = words[: max(1, int(len(words) * 0.7))]
        if tail and sum(tail) / len(tail) < (sum(head) / len(head)) * 0.45:
            errors.append("paragraph-density collapse near block ending")
    return errors
