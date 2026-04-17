from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import AsyncSessionCompat
from app.models import Ontology, World


class WorldService:
    def list_worlds(self, session: Session) -> list[tuple[World, list[int]]]:
        worlds = session.execute(select(World)).scalars().all()
        ontology_pairs = session.execute(select(Ontology.world_id, Ontology.id)).all()

        by_world: dict[str, list[int]] = {}
        for world_id, ontology_id in ontology_pairs:
            by_world.setdefault(world_id, []).append(ontology_id)

        return [(world, by_world.get(world.id, [])) for world in worlds]

    def get_world(self, session: Session, world_id: str) -> tuple[World, list[int]] | None:
        world = session.execute(select(World).where(World.id == world_id)).scalar_one_or_none()
        if world is None:
            return None
        ontologies = session.execute(select(Ontology.id).where(Ontology.world_id == world_id)).scalars().all()
        return world, list(ontologies)

    async def list_worlds_async(self, session: AsyncSessionCompat) -> list[tuple[World, list[int]]]:
        worlds = (await session.execute(select(World))).scalars().all()
        ontology_pairs = (await session.execute(select(Ontology.world_id, Ontology.id))).all()

        by_world: dict[str, list[int]] = {}
        for world_id, ontology_id in ontology_pairs:
            by_world.setdefault(world_id, []).append(ontology_id)

        return [(world, by_world.get(world.id, [])) for world in worlds]

    async def get_world_async(self, session: AsyncSessionCompat, world_id: str) -> tuple[World, list[int]] | None:
        world = (await session.execute(select(World).where(World.id == world_id))).scalar_one_or_none()
        if world is None:
            return None
        ontologies = (
            await session.execute(select(Ontology.id).where(Ontology.world_id == world_id))
        ).scalars().all()
        return world, list(ontologies)


world_service = WorldService()
