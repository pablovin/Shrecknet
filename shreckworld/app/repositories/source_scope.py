"""Direct ontology scope, shared by source operations and retrieval.

OntologyScope is request metadata, never a persisted world/library container.
Legacy string scopes remain available only for existing collection contracts.
"""
from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement
from app.models import ShreckWorld, ShreckWorldLibraryItem


class OntologyScope(str):
    ontology_id: int
    name: str
    description: str | None
    web_search_enabled = False
    web_search_policy = "disabled"

    def __new__(cls, ontology_id: int, name: str, description: str | None = None):
        scope = super().__new__(cls, f"ontology:{ontology_id}")
        scope.ontology_id, scope.name, scope.description = ontology_id, name, description
        return scope

    @property
    def id(self) -> OntologyScope:
        return self


def source_scope(session: Session, scope: str) -> ShreckWorld | OntologyScope | None:
    return scope if isinstance(scope, OntologyScope) else session.get(ShreckWorld, scope)


def item_scope_clause(scope: str) -> ColumnElement[bool]:
    if isinstance(scope, OntologyScope):
        return ShreckWorldLibraryItem.ontology_id == scope.ontology_id
    return ShreckWorldLibraryItem.shreckworld_id == scope


def item_in_scope(item: ShreckWorldLibraryItem, scope: str) -> bool:
    if isinstance(scope, OntologyScope):
        return item.ontology_id == scope.ontology_id
    return item.shreckworld_id == scope
