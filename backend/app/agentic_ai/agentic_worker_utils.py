"""Utility helpers for agentic workers."""
from __future__ import annotations

import unicodedata
from typing import Iterable, List

from bs4 import BeautifulSoup
from langchain_text_splitters import RecursiveCharacterTextSplitter


def strip_html(text: str | None) -> str:
    """Return visible text from HTML string."""
    soup = BeautifulSoup(text or "", "html.parser")
    return soup.get_text(separator=" ", strip=True)


def normalize_name(name: str | None) -> str:
    """Normalize names for fuzzy matching.

    Removes accents, lowercases, trims, and drops common prefixes like articles
    and honorifics so that similar names can be compared reliably.
    """
    if not name:
        return ""
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ASCII", "ignore").decode("ASCII")
    name = name.lower().strip()
    for prefix in ["o ", "a ", "os ", "as ", "barão ", "lady ", "rei ", "rainha "]:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name


def ensure_visible_text(chunk: str) -> str:
    """Return text content from an HTML chunk."""
    return BeautifulSoup(chunk, "html.parser").get_text(separator=" ", strip=True)


def split_html_by_headers(
    html: str,
    header_tags: Iterable[str] = ("h1", "h2", "h3"),
    fallback_chunk_size: int = 1000,
    fallback_overlap: int = 200,
) -> List[str]:
    """Split an HTML document by header tags or fall back to a text splitter."""
    soup = BeautifulSoup(html, "html.parser")
    headers = []
    for tag in header_tags:
        headers += soup.find_all(tag)
    headers = sorted(
        headers,
        key=lambda x: x.sourceline if hasattr(x, "sourceline") and x.sourceline else 0,
    )

    if len(headers) > 1:
        chunks: List[str] = []
        for i, h in enumerate(headers):
            section_texts = [h.get_text(separator=" ", strip=True)]
            for sib in h.next_siblings:
                if getattr(sib, "name", None) in header_tags:
                    break
                if getattr(sib, "get_text", None):
                    txt = sib.get_text(separator=" ", strip=True)
                    if txt:
                        section_texts.append(txt)
                elif isinstance(sib, str):
                    stripped = sib.strip()
                    if stripped:
                        section_texts.append(stripped)
            chunk = "\n".join(section_texts).strip()
            if chunk:
                chunks.append(chunk)
        return [ensure_visible_text(c) for c in chunks]

    visible_text = ensure_visible_text(html)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=fallback_chunk_size * 8,
        chunk_overlap=fallback_overlap * 8,
    )
    return splitter.split_text(visible_text)
