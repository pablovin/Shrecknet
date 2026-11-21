"""Resolve a canonical world's shared graph and source scope through Shrecknet."""

from urllib.parse import quote

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

from app.integrations.ontologies import shrecknet_read


class WorldScope(BaseModel):
    id: str
    name: str
    active_ontology_id: int | None
    description: str | None = None


def resolve_world(world_id: str, token: str, ontology_id: int | None = None) -> WorldScope:
    try:
        world = WorldScope.model_validate(shrecknet_read(f"/contracts/worlds/{quote(world_id, safe='')}", token))
    except ValidationError as exc:
        raise HTTPException(503, "Invalid Shrecknet world response") from exc
    if world.id != world_id:
        raise HTTPException(503, "Invalid Shrecknet world response")
    if world.active_ontology_id is None:
        raise HTTPException(409, "Selected world has no unambiguous active ontology")
    if ontology_id is not None and world.active_ontology_id != ontology_id:
        raise HTTPException(409, "Ontology does not match the world's active ontology")
    return world
