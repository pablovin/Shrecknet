"""Conservative, source-scoped terminology normalization before query planning."""
from __future__ import annotations

import re
from collections import Counter

from app.schemas.query_v3 import QueryCorrection, QueryInterpretation
from app.services.query import RetrievalScope

_TOKEN = re.compile(r"\b[A-Za-z][A-Za-z'-]{3,}\b")
_RECOVERY_CONTEXT = re.compile(r"\b(?:heal|healing|recover|recovery|restore|restoring)\b", re.I)
_STOPWORDS = {
    "about", "after", "again", "also", "does", "from", "have", "into", "that",
    "their", "there", "these", "they", "this", "those", "what", "when", "where",
    "which", "while", "with", "would",
}


def _edit_distance_one(left: str, right: str) -> bool:
    if left == right:
        return False
    if abs(len(left) - len(right)) > 1:
        return False
    i = j = differences = 0
    while i < len(left) and j < len(right):
        if left[i] == right[j]:
            i += 1
            j += 1
            continue
        differences += 1
        if differences > 1:
            return False
        if len(left) == len(right):
            if i + 1 < len(left) and j + 1 < len(right) and left[i] == right[j + 1] and left[i + 1] == right[j]:
                i += 2
                j += 2
                continue
            i += 1
            j += 1
        elif len(left) > len(right):
            i += 1
        else:
            j += 1
    return differences + int(i < len(left) or j < len(right)) == 1


def interpret_query(query: str, scope: RetrievalScope) -> QueryInterpretation:
    terms: Counter[str] = Counter()
    display: dict[str, str] = {}
    headings: set[str] = set()
    for chunk, item in scope.rows:
        for term in _TOKEN.findall(f"{item.title} {chunk.text} {' '.join(chunk.heading_path)}"):
            folded = term.casefold()
            if folded not in _STOPWORDS:
                terms[folded] += 1
                display.setdefault(folded, term)
        headings.update(part.casefold() for part in chunk.heading_path)

    replacements: list[tuple[int, int, str, QueryCorrection]] = []
    context_verified = bool(_RECOVERY_CONTEXT.search(query))
    for match in _TOKEN.finditer(query):
        original = match.group()
        folded = original.casefold()
        if folded in terms:
            continue
        if original.isupper() and folded == "san" and "sanity" in terms:
            replacements.append((
                match.start(), match.end(), display.get("sanity", "Sanity"),
                QueryCorrection(
                    original=original, replacement=display.get("sanity", "Sanity"),
                    confidence=0.99, basis="known_alias",
                ),
            ))
            continue
        if not context_verified or not original[0].isupper() or len(folded) < 5:
            continue
        candidates = [
            term for term, count in terms.items()
            if (count >= 2 or term in headings) and _edit_distance_one(folded, term)
        ]
        if len(candidates) != 1:
            continue
        candidate = candidates[0]
        replacement = display[candidate]
        replacements.append((
            match.start(), match.end(), replacement,
            QueryCorrection(
                original=original, replacement=replacement,
                confidence=0.97 if candidate in headings else 0.92,
                basis="context_verified" if candidate in headings else "vocabulary_fuzzy",
            ),
        ))

    interpreted = query
    for start, end, replacement, _ in reversed(replacements):
        interpreted = interpreted[:start] + replacement + interpreted[end:]
    return QueryInterpretation(
        original_query=query,
        interpreted_query=interpreted,
        corrections=[item[3] for item in replacements],
    )
