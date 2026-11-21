from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import AsyncSessionCompat
from app.models import Ontology, World


WORLD_ONTOLOGY_METADATA = {"name", "description", "image_url", "rpg_system"}


class WorldService:
    async def create_world(
        self, session: AsyncSessionCompat, data: dict
    ) -> tuple[World, list[int]]:
        world = World(id=str(uuid4()), **data)
        ontology = Ontology(world=world, **{
            key: value for key, value in data.items() if key in WORLD_ONTOLOGY_METADATA
        })
        session.add(world)
        await session.flush()
        ontology_id = ontology.id
        await session.commit()
        await session.refresh(world)
        return world, [ontology_id]

    async def update_world(
        self, session: AsyncSessionCompat, world_id: str, data: dict
    ) -> tuple[World, list[int]] | None:
        result = await self.get_world_async(session, world_id)
        if result is None:
            return None
        world, ids = result
        for key, value in data.items():
            setattr(world, key, value)
        if len(ids) == 1:
            ontology = await session.get(Ontology, ids[0])
            for key, value in data.items():
                if key in WORLD_ONTOLOGY_METADATA:
                    setattr(ontology, key, value)
        await session.commit()
        await session.refresh(world)
        return world, ids

    def list_worlds(self, session: Session) -> list[tuple[World, list[int]]]:
        worlds = session.execute(select(World)).scalars().all()
        ontology_pairs = session.execute(select(Ontology.world_id, Ontology.id)).all()

        by_world: dict[str, list[int]] = {}
        for world_id, ontology_id in ontology_pairs:
            by_world.setdefault(world_id, []).append(ontology_id)

        return [(world, sorted(by_world.get(world.id, []))) for world in worlds]

    def get_world(self, session: Session, world_id: str) -> tuple[World, list[int]] | None:
        world = session.execute(select(World).where(World.id == world_id)).scalar_one_or_none()
        if world is None:
            return None
        ontologies = session.execute(select(Ontology.id).where(Ontology.world_id == world_id)).scalars().all()
        return world, sorted(ontologies)

    async def list_worlds_async(self, session: AsyncSessionCompat) -> list[tuple[World, list[int]]]:
        worlds = (await session.execute(select(World))).scalars().all()
        ontology_pairs = (await session.execute(select(Ontology.world_id, Ontology.id))).all()

        by_world: dict[str, list[int]] = {}
        for world_id, ontology_id in ontology_pairs:
            by_world.setdefault(world_id, []).append(ontology_id)

        return [(world, sorted(by_world.get(world.id, []))) for world in worlds]

    async def get_world_async(self, session: AsyncSessionCompat, world_id: str) -> tuple[World, list[int]] | None:
        world = (await session.execute(select(World).where(World.id == world_id))).scalar_one_or_none()
        if world is None:
            return None
        ontologies = (
            await session.execute(select(Ontology.id).where(Ontology.world_id == world_id))
        ).scalars().all()
        return world, sorted(ontologies)


world_service = WorldService()
