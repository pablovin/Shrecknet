from __future__ import annotations

import re
from typing import Iterable, Sequence
import logging


STOPWORDS = {
    "mr",
    "mrs",
    "ms",
    "miss",
    "dr",
    "prof",
    "professor",
    "sir",
    "dame",
    # common stopwords to avoid noisy matches
    "the",
    "and",
    "of",
    "in",
    "on",
    "at",
    "for",
    "to",
    "a",
    "an",
    "with",
    "by",
    "from",
}


def _tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", name.lower().strip()) if t]


def _ngrams(tokens: Sequence[str]) -> list[str]:
    grams: list[str] = []
    filtered = [t for t in tokens if t not in STOPWORDS]
    n = len(filtered)
    for i in range(n):
        for j in range(i + 2, n + 1):  # length >= 2
            grams.append(" ".join(filtered[i:j]))
    return grams


def build_alias_pattern(aliases: Iterable[str]) -> re.Pattern[str] | None:
    """Build a regex pattern matching aliases, tokens and multi-word n-grams.

    - Adds cleaned aliases (underscores/punct as spaces), tokens and 2+ word n-grams
    - Drops common honorifics (stopwords) from n-grams
    - Sorts longest-first for greedy replacement
    """
    expanded: set[str] = set()
    for alias in aliases:
        if not alias:
            continue
        cleaned = alias.lower().strip()
        if not cleaned:
            continue
        expanded.add(cleaned.replace("_", " "))
        toks = _tokens(cleaned)
        for t in toks:
            if len(t) >= 3 and t not in STOPWORDS:
                expanded.add(t)
        for gram in _ngrams(toks):
            expanded.add(gram)
    alias_list = list(expanded)
    if not alias_list:
        return None
    alias_list.sort(key=len, reverse=True)
    escaped = [re.escape(alias) for alias in alias_list]
    pattern = r"(?<!\w)(" + "|".join(escaped) + r")(?!\w)"
    return re.compile(pattern, flags=re.IGNORECASE)


def link_text(
    text: str | None,
    alias_targets: dict[str, list[dict]],
    current_entity_id: str,
    current_instance_id: str,
    pattern: re.Pattern[str] | None,
) -> str | None:
    if not text or pattern is None:
        return text

    logger = logging.getLogger("ontology_linker")

    def replacement(match: re.Match[str]) -> str:
        matched_text = match.group(0)
        normalized = matched_text.lower()
        targets = alias_targets.get(normalized)
        if not targets:
            return matched_text
        for t in targets:
            target_id = t.get("entity_id")
            target_instance_id = t.get("instance_id") or current_instance_id
            target_alias_full = t.get("alias") or normalized
            if target_id != current_entity_id:
                logger.info(
                    "link: instance=%s source=%s target_instance=%s target_alias=%s word='%s' (exact)",
                    current_instance_id,
                    current_entity_id,
                    target_instance_id,
                    target_alias_full,
                    matched_text,
                )
                return (
                    f'<a data-ontology-instance="{target_instance_id}" '
                    f'data-entity-alias="{target_alias_full}">{matched_text}</a>'
                )
        return matched_text

    # Avoid decorating inside already-decorated anchors
    anchor_re = re.compile(
        r"(<a\b[^>]*data-ontology-instance=\"[^\"]+\"[^>]*>.*?</a>)",
        flags=re.IGNORECASE | re.DOTALL,
    )

    parts = anchor_re.split(text)

    def replace_exact_segment(segment: str) -> str:
        return pattern.sub(replacement, segment)

    replaced_parts = [
        part if idx % 2 == 1 else replace_exact_segment(part)
        for idx, part in enumerate(parts)
    ]
    replaced = "".join(replaced_parts)

    # Fuzzy fallback: if no exact replacements happened, attempt edit-distance<=1 on words
    if replaced == text:
        words = list(re.finditer(r"\b[\w']{5,}\b", text))
        keys = list(alias_targets.keys())

        def ed1(a: str, b: str) -> bool:
            a, b = a.lower(), b.lower()
            if abs(len(a) - len(b)) > 1:
                return False
            # Early exit for equality
            if a == b:
                return True
            # Ensure a is shorter
            if len(a) > len(b):
                a, b = b, a
            i = j = diffs = 0
            while i < len(a) and j < len(b):
                if a[i] == b[j]:
                    i += 1
                    j += 1
                    continue
                diffs += 1
                if diffs > 1:
                    return False
                # Try substitution or insertion in b
                if len(a) == len(b):
                    i += 1
                    j += 1
                else:
                    j += 1
            # Account for trailing char
            diffs += (len(b) - j) + (len(a) - i)
            return diffs <= 1

        def fuzzy_replace_segment(segment: str) -> str:
            seg_words = list(re.finditer(r"\b[\w']{5,}\b", segment))
            out: list[str] = []
            last = 0
            for m in seg_words:
                w = m.group(0)
                normw = w.lower()
                target = None
                for k in keys:
                    if len(k) >= 5 and ed1(normw, k):
                        # choose the first non-self target
                        for t in alias_targets.get(k, []):
                            if t.get("entity_id") != current_entity_id:
                                target = t
                                break
                    if target:
                        break
                if target:
                    out.append(segment[last : m.start()])
                    logger.info(
                        "link: instance=%s source=%s target_instance=%s target_alias=%s word='%s' (fuzzy)",
                        current_instance_id,
                        current_entity_id,
                        target.get("instance_id") or current_instance_id,
                        target.get("alias") or normw,
                        w,
                    )
                    out.append(
                        f'<a data-ontology-instance="{target.get("instance_id") or current_instance_id}" '
                        f'data-entity-alias="{target.get("alias") or normw}">{w}</a>'
                    )
                    last = m.end()
            if out:
                out.append(segment[last:])
                return "".join(out)
            return segment

        # Apply fuzzy replacement only to non-anchor segments
        fuzzy_parts = [
            part if idx % 2 == 1 else fuzzy_replace_segment(part)
            for idx, part in enumerate(parts)
        ]
        return "".join(fuzzy_parts)

    return replaced
