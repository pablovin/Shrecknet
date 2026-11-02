"""Explicit execution identity and resolved world scope for the built-in Elder."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ElderExecutionContext:
    ontology_ids: tuple[int, ...]
    id: str = "elder"
    name: str = "Elder"
    writing_style: str = "Answer clearly using the supplied evidence. Identify the relevant world when comparing worlds."
