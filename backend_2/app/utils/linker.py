from __future__ import annotations

import re
from typing import Iterable


def build_alias_pattern(aliases: Iterable[str]) -> re.Pattern[str] | None:
    alias_list = [alias for alias in aliases if alias]
    if not alias_list:
        return None
    alias_list.sort(key=len, reverse=True)
    escaped = [re.escape(alias) for alias in alias_list]
    pattern = r"(?<!\w)(" + "|".join(escaped) + r")(?!\w)"
    return re.compile(pattern, flags=re.IGNORECASE)


def link_text(
    text: str | None,
    alias_to_ids: dict[str, list[str]],
    current_entity_id: str,
    instance_id: str,
    pattern: re.Pattern[str] | None,
) -> str | None:
    if not text or pattern is None:
        return text

    def replacement(match: re.Match[str]) -> str:
        matched_text = match.group(0)
        normalized = matched_text.lower()
        target_ids = alias_to_ids.get(normalized)
        if not target_ids:
            return matched_text
        for target_id in target_ids:
            if target_id != current_entity_id:
                return (
                    f'<a data-ontology-instance="{instance_id}" '
                    f'data-entity-id="{target_id}">{matched_text}</a>'
                )
        return matched_text

    return pattern.sub(replacement, text)
