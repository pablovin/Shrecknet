"""One live ruleset selection for every table in a Shreckworld."""

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.game_engine import GameBinding, GameRulesetVersion, WorldRuleset
from app.models.shrecktables import Table


class WorldRulesRead(BaseModel):
    version_id: str | None
    revision: int
    legacy_conflict: bool = False


def read_world_rules(db: Session, world_id: str) -> WorldRulesRead:
    selection = db.get(WorldRuleset, world_id)
    if selection is not None:
        return WorldRulesRead(version_id=selection.version_id, revision=selection.revision)

    # Existing table pins are migration evidence. All tables observe the same
    # result. A manager can adopt a version or explicitly clear the selection.
    pins = db.execute(select(Table.ontology_id, Table.version_id).where(
        Table.world_id == world_id, Table.version_id.is_not(None)
    ).distinct()).all()
    if not pins:
        return WorldRulesRead(version_id=None, revision=0)
    version = db.get(GameRulesetVersion, pins[0].version_id) if len(pins) == 1 else None
    if version is None or version.status not in {"published", "superseded"}:
        return WorldRulesRead(version_id=None, revision=0, legacy_conflict=True)
    return WorldRulesRead(version_id=version.id, revision=0)


def select_world_rules(
    db: Session, world_id: str, ontology_id: int, version_id: str | None,
    expected_revision: int, actor_id: str,
) -> WorldRulesRead:
    if version_id is not None:
        binding = db.scalar(
            select(GameBinding.id).join(GameRulesetVersion, GameRulesetVersion.ruleset_id == GameBinding.ruleset_id)
            .where(
                GameRulesetVersion.id == version_id, GameRulesetVersion.status == "published",
                GameBinding.ontology_id == ontology_id,
            )
        )
        if binding is None:
            raise HTTPException(409, "Select a published ruleset version bound to this world")

    new_revision = expected_revision + 1
    if expected_revision == 0:
        db.add(WorldRuleset(
            world_id=world_id, version_id=version_id, revision=new_revision, updated_by=actor_id
        ))
    else:
        changed = db.execute(update(WorldRuleset).where(
            WorldRuleset.world_id == world_id, WorldRuleset.revision == expected_revision
        ).values(version_id=version_id, revision=new_revision, updated_by=actor_id))
        if changed.rowcount != 1:
            raise HTTPException(409, "World rules revision conflict")
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "World rules revision conflict") from exc
    return WorldRulesRead(version_id=version_id, revision=new_revision)
