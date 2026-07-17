"""Lossless, tokenizer-aware chunking for semantic memory documents."""

from __future__ import annotations

import re
from typing import Any

from app.graphrag.embedding_service import document_embedding_text, get_embedding_model


class LosslessTokenChunker:
    def __init__(self, *, target_tokens: int, overlap_tokens: int) -> None:
        self.target_tokens = max(32, int(target_tokens))
        self.overlap_tokens = max(0, min(int(overlap_tokens), self.target_tokens // 2))

    @property
    def tokenizer(self) -> Any:
        return get_embedding_model().tokenizer

    def token_count(self, text: str) -> int:
        return len(self.tokenizer.encode(text, add_special_tokens=True, truncation=False))

    def split(self, text: str, *, header: str = "") -> list[str]:
        """Split all input text without model-side truncation or content loss."""
        text = (text or "").strip()
        if not text:
            return []
        prefix = f"{header.strip()}\n\n" if header.strip() else ""
        if self.token_count(document_embedding_text(prefix + text)) <= self.target_tokens:
            return [text]

        units = [unit for unit in re.split(r"(?<=\n)\s*\n+", text) if unit]
        if len(units) == 1:
            units = [unit for unit in re.split(r"(?<=[.!?])\s+", text) if unit]

        chunks: list[str] = []
        current: list[str] = []
        for unit in units:
            candidate = "\n\n".join(current + [unit]).strip()
            if current and self.token_count(document_embedding_text(prefix + candidate)) > self.target_tokens:
                chunks.extend(self._fit("\n\n".join(current), prefix))
                current = [unit]
            else:
                current.append(unit)
        if current:
            chunks.extend(self._fit("\n\n".join(current), prefix))
        return [chunk for chunk in chunks if chunk.strip()]

    def _fit(self, text: str, prefix: str) -> list[str]:
        if self.token_count(document_embedding_text(prefix + text)) <= self.target_tokens:
            return [text.strip()]
        tokenizer = self.tokenizer
        prefix_ids = tokenizer.encode(
            document_embedding_text(prefix), add_special_tokens=False, truncation=False
        )
        available = max(16, self.target_tokens - len(prefix_ids) - 2)
        ids = tokenizer.encode(text, add_special_tokens=False, truncation=False)
        step = max(1, available - self.overlap_tokens)
        windows: list[str] = []
        start = 0
        while start < len(ids):
            end = min(len(ids), start + available)
            decoded = tokenizer.decode(ids[start:end], skip_special_tokens=True).strip()
            if decoded:
                while (
                    self.token_count(document_embedding_text(prefix + decoded)) > self.target_tokens
                    and end > start + 1
                ):
                    end -= 1
                    decoded = tokenizer.decode(ids[start:end], skip_special_tokens=True).strip()
                if decoded:
                    windows.append(decoded)
            if end >= len(ids):
                break
            start = max(start + 1, end - self.overlap_tokens)
        return windows
