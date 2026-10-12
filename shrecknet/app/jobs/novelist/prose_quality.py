"""Local completion checks and safe HTML rendering for Novelist v5 prose."""

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
    minimum_words = max(40, int(target_words * 0.35))
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


# Explicit administrative phrases, rather than broad words like "roll" or "glory".
_GAMEPLAY = re.compile(
    r"\b(?:roll (?:a |the )?d(?:4|6|8|10|12|20|100)|character sheet|"
    r"glory points?|initiative roll|saving throw|experience points?|\d+d(?:4|6|8|10|12|20|100)|"
    r"scheda (?:del personaggio|personaggio)|punti gloria|tiro (?:di iniziativa|salvezza)|"
    r"fiche de personnage|points de gloire|jet (?:de sauvegarde|d’initiative))\b", re.IGNORECASE,
)


def validate_contextual_prose(
    text: str, *, forbidden_names: list[str], canonical_names: list[str],
    accepted_sections: list[str],
) -> list[str]:
    """Conservative local leakage/repetition checks, never semantic verification."""
    errors = []
    canonical = {name.casefold() for name in canonical_names}
    for name in forbidden_names:
        if name.casefold() not in canonical and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.IGNORECASE):
            errors.append(f"nonfiction identity appears in prose: {name}")
    if _GAMEPLAY.search(text):
        errors.append("explicit RPG mechanics or gameplay administration appears in prose")
    paragraphs = [" ".join(part.casefold().split()) for part in re.split(r"\n\s*\n", text)]
    substantial = [part for part in paragraphs if len(part.split()) >= 40]
    if len(substantial) != len(set(substantial)):
        errors.append("narrative section repeats within the current unit")
    normalized = " ".join(text.casefold().split())
    # Long exact spans catch repeated paragraphs/openings without fuzzy semantic claims.
    for accepted in accepted_sections:
        prior = " ".join(accepted.casefold().split())
        words = prior.split()
        if normalized == prior or any(" ".join(words[i:i + 40]) in normalized for i in range(max(0, len(words) - 39))):
            errors.append("narrative section repeats previously accepted prose")
            break
    return errors
