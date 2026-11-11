from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models.game_engine import (
    GameBinding, GameDefinition, GameGenerationRun, GameRuleset, GameRulesetReview, GameRulesetVersion,
    ShreckTable, ShreckTableParticipant,
)
from app.models.document import (
    ShreckWorldDocument, ShreckWorldDocumentBlock, ShreckWorldDocumentPage,
    ShreckWorldDocumentSection,
)
from app.models.shreckworld import ShreckWorldLibraryItem
from app.schemas.game_engine import BindingCreate, DefinitionCreate, RulesetCreate, TableCreate, TableUpdate


def now() -> datetime:
    return datetime.now(timezone.utc)


def _cursor(created_at: datetime, row_id: str) -> str:
    raw = f"{created_at.replace(tzinfo=None).isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str | None) -> tuple[datetime, str] | None:
    if value is None:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        timestamp, row_id = base64.urlsafe_b64decode(padded).decode().split("|", 1)
        # SQLite returns naive timestamps even for timezone-aware columns.
        return datetime.fromisoformat(timestamp).replace(tzinfo=None), row_id
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(422, "Invalid pagination cursor") from exc


def create_ruleset(session: Session, payload: RulesetCreate, owner_id: str) -> GameRuleset:
    row = GameRuleset(name=payload.name, edition=payload.edition, schema_version=payload.schema_version,
                      definition_revision=1, status="draft", created_by=owner_id)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def list_rulesets(session: Session) -> list[GameRuleset]:
    return list(session.scalars(select(GameRuleset).order_by(GameRuleset.created_at.desc(), GameRuleset.id)))


def create_binding(session: Session, payload: BindingCreate, owner_id: str) -> GameBinding:
    if not session.get(GameRuleset, payload.ruleset_id):
        raise HTTPException(404, "Ruleset not found")
    row = GameBinding(ontology_id=payload.ontology_id, ruleset_id=payload.ruleset_id,
                      status="draft", created_by=owner_id)
    session.add(row)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "Binding already exists") from exc
    session.refresh(row)
    return row


def list_bindings(session: Session, ontology_id: int) -> list[GameBinding]:
    return list(session.scalars(select(GameBinding).where(GameBinding.ontology_id == ontology_id)
                                .order_by(GameBinding.created_at, GameBinding.id)))


def add_definition(session: Session, ruleset_id: str, payload: DefinitionCreate) -> GameDefinition:
    ruleset = session.get(GameRuleset, ruleset_id)
    if not ruleset:
        raise HTTPException(404, "Ruleset not found")
    if ruleset.status != "draft":
        raise HTTPException(409, "Ruleset is not editable")
    if payload.schema_version != ruleset.schema_version:
        raise HTTPException(422, "Definition schema version does not match the ruleset")
    row = GameDefinition(ruleset_id=ruleset_id, kind=payload.kind,
                         schema_version=payload.schema_version, payload=payload.payload,
                         provenance=[reference.model_dump(mode="json") for reference in payload.provenance], status="draft")
    ruleset.definition_revision += 1
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def definitions_for_ruleset(session: Session, ruleset_id: str) -> tuple[GameRuleset, list[GameDefinition]]:
    ruleset = session.get(GameRuleset, ruleset_id)
    if not ruleset:
        raise HTTPException(404, "Ruleset not found")
    rows = list(session.scalars(select(GameDefinition).where(GameDefinition.ruleset_id == ruleset_id)
                                .order_by(GameDefinition.created_at, GameDefinition.id)))
    return ruleset, rows


def approve_ruleset(session: Session, ruleset_id: str, expected_revision: int, reviewer_id: str) -> GameRuleset:
    ruleset, definitions = definitions_for_ruleset(session, ruleset_id)
    if ruleset.status != "draft":
        raise HTTPException(409, "Only a draft ruleset can be reviewed")
    if ruleset.definition_revision != expected_revision:
        raise HTTPException(409, "Ruleset revision conflict")
    if not definitions:
        raise HTTPException(422, "A ruleset needs at least one definition before review")
    ruleset.status = "approved"
    ruleset.approved_by = reviewer_id
    ruleset.approved_at = now()
    session.add(GameRulesetReview(ruleset_id=ruleset_id, definition_revision=expected_revision,
                                  decision="approved", reviewer_id=reviewer_id))
    session.commit()
    session.refresh(ruleset)
    return ruleset


def reject_ruleset(session: Session, ruleset_id: str, expected_revision: int, reviewer_id: str, reason: str) -> GameRuleset:
    ruleset, definitions = definitions_for_ruleset(session, ruleset_id)
    if ruleset.status != "draft":
        raise HTTPException(409, "Only a draft ruleset can be reviewed")
    if ruleset.definition_revision != expected_revision:
        raise HTTPException(409, "Ruleset revision conflict")
    if not definitions:
        raise HTTPException(422, "A ruleset needs at least one definition before review")
    session.add(GameRulesetReview(ruleset_id=ruleset_id, definition_revision=expected_revision,
                                  decision="rejected", reviewer_id=reviewer_id, reason=reason))
    session.commit()
    session.refresh(ruleset)
    return ruleset


def list_reviews(session: Session, ruleset_id: str) -> list[GameRulesetReview]:
    if not session.get(GameRuleset, ruleset_id):
        raise HTTPException(404, "Ruleset not found")
    return list(session.scalars(select(GameRulesetReview).where(GameRulesetReview.ruleset_id == ruleset_id)
                                .order_by(GameRulesetReview.created_at.desc(), GameRulesetReview.id)))


def publish_ruleset(session: Session, ruleset_id: str, expected_revision: int, publisher_id: str,
                    supersedes_version_id: str | None) -> GameRulesetVersion:
    ruleset, definitions = definitions_for_ruleset(session, ruleset_id)
    if ruleset.status != "approved":
        raise HTTPException(409, "Only an approved ruleset can be published")
    if ruleset.definition_revision != expected_revision:
        raise HTTPException(409, "Ruleset revision conflict")
    if not definitions:
        raise HTTPException(422, "A ruleset needs at least one definition before publication")
    if supersedes_version_id:
        previous = session.get(GameRulesetVersion, supersedes_version_id)
        if not previous or previous.ruleset_id != ruleset_id or previous.status not in {"published", "superseded"}:
            raise HTTPException(422, "Superseded version is invalid")
        previous.status = "superseded"
    snapshot = [{"id": row.id, "kind": row.kind, "schema_version": row.schema_version,
                 "payload": row.payload, "provenance": row.provenance} for row in definitions]
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    version = GameRulesetVersion(
        ruleset_id=ruleset_id, definition_revision=ruleset.definition_revision,
        schema_version=ruleset.schema_version, definition_digest=hashlib.sha256(canonical).hexdigest(),
        definitions_snapshot=snapshot, dependency_pins=[], status="published", published_by=publisher_id,
        supersedes_version_id=supersedes_version_id,
    )
    ruleset.status = "published"
    session.add(version)
    session.commit()
    session.refresh(version)
    return version


def list_versions(session: Session, ruleset_id: str) -> list[GameRulesetVersion]:
    if not session.get(GameRuleset, ruleset_id):
        raise HTTPException(404, "Ruleset not found")
    return list(session.scalars(select(GameRulesetVersion).where(GameRulesetVersion.ruleset_id == ruleset_id)
                                .order_by(GameRulesetVersion.published_at.desc(), GameRulesetVersion.id)))


def get_binding(session: Session, binding_id: str, ontology_id: int) -> GameBinding:
    row = session.scalar(select(GameBinding).where(GameBinding.id == binding_id, GameBinding.ontology_id == ontology_id))
    if not row:
        raise HTTPException(404, "Binding not found")
    return row


def validate_source_references(session: Session, ontology_id: int, references: list[dict]) -> None:
    for reference in references:
        if reference["document_id"] != reference["document_revision_id"]:
            raise HTTPException(422, "Source revision reference is invalid")
        row = session.execute(select(ShreckWorldDocument.id).join(
            ShreckWorldLibraryItem, ShreckWorldLibraryItem.id == ShreckWorldDocument.library_item_id,
        ).where(
            ShreckWorldDocument.id == reference["document_revision_id"],
            ShreckWorldDocument.library_item_id == reference["source_id"],
            ShreckWorldDocument.source_sha256 == reference["content_digest"],
            ShreckWorldLibraryItem.ontology_id == ontology_id,
        )).first()
        if row is None:
            raise HTTPException(422, "Source revision is unavailable or does not belong to this ontology")
        locator = reference["locator"]
        kind = reference["locator_kind"]
        if kind == "physical_page":
            try:
                page_number = int(locator)
            except ValueError as exc:
                raise HTTPException(422, "Physical page locator must be a positive page number") from exc
            located = page_number > 0 and session.scalar(select(ShreckWorldDocumentPage.id).where(
                ShreckWorldDocumentPage.document_id == reference["document_id"],
                ShreckWorldDocumentPage.page_number == page_number,
            )) is not None
        elif kind == "printed_page":
            located = session.scalar(select(ShreckWorldDocumentPage.id).where(
                ShreckWorldDocumentPage.document_id == reference["document_id"],
                ShreckWorldDocumentPage.page_label == locator,
            )) is not None
        elif kind == "section":
            located = session.scalar(select(ShreckWorldDocumentSection.id).where(
                ShreckWorldDocumentSection.document_id == reference["document_id"],
                ShreckWorldDocumentSection.id == locator,
            )) is not None
        else:
            located = session.scalar(select(ShreckWorldDocumentBlock.id).where(
                ShreckWorldDocumentBlock.document_id == reference["document_id"],
                ShreckWorldDocumentBlock.id == locator,
            )) is not None
        if not located:
            raise HTTPException(422, "Source locator does not exist in the selected document revision")


def create_table(session: Session, payload: TableCreate, owner_id: str) -> ShreckTable:
    version = session.get(GameRulesetVersion, payload.version_id)
    if not version or version.status != "published":
        raise HTTPException(409, "A published ruleset version is required")
    row = ShreckTable(ontology_id=payload.ontology_id, version_id=payload.version_id,
                      name=payload.name, status="active", revision=1, created_by=owner_id)
    session.add(row)
    session.commit()
    return get_table(session, row.id, payload.ontology_id)


def get_table(session: Session, table_id: str, ontology_id: int) -> ShreckTable:
    row = session.scalar(select(ShreckTable).options(selectinload(ShreckTable.participants))
                         .where(ShreckTable.id == table_id, ShreckTable.ontology_id == ontology_id))
    if not row:
        raise HTTPException(404, "ShreckTable not found")
    return row


def list_tables(session: Session, ontology_id: int, limit: int, cursor: str | None) -> tuple[list[ShreckTable], str | None]:
    query = select(ShreckTable).options(selectinload(ShreckTable.participants)).where(ShreckTable.ontology_id == ontology_id)
    decoded = _decode_cursor(cursor)
    if decoded:
        created_at, row_id = decoded
        query = query.where(or_(ShreckTable.created_at > created_at,
                                and_(ShreckTable.created_at == created_at, ShreckTable.id > row_id)))
    rows = list(session.scalars(query.order_by(ShreckTable.created_at, ShreckTable.id).limit(limit + 1)).unique())
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = _cursor(rows[-1].created_at, rows[-1].id) if more and rows else None
    return rows, next_cursor


def update_table(session: Session, table_id: str, ontology_id: int, revision: int, payload: TableUpdate) -> ShreckTable:
    row = get_table(session, table_id, ontology_id)
    if row.status != "active":
        raise HTTPException(410, "ShreckTable is archived")
    result = session.execute(update(ShreckTable).where(
        ShreckTable.id == table_id, ShreckTable.ontology_id == ontology_id,
        ShreckTable.revision == revision, ShreckTable.status == "active",
    ).values(name=payload.name, revision=ShreckTable.revision + 1, updated_at=now()))
    if result.rowcount != 1:
        session.rollback()
        current = get_table(session, table_id, ontology_id)
        raise HTTPException(410 if current.status != "active" else 409,
                            "ShreckTable is archived" if current.status != "active" else "Table revision conflict")
    session.commit()
    return get_table(session, table_id, ontology_id)


def archive_table(session: Session, table_id: str, ontology_id: int) -> ShreckTable:
    row = get_table(session, table_id, ontology_id)
    if row.status == "active":
        row.status, row.archived_at = "archived", now()
        row.revision += 1
        session.commit()
    return get_table(session, table_id, ontology_id)


def add_participant(session: Session, table_id: str, ontology_id: int, *, user_id: str, role: str,
                    character_agent_id: str | None) -> ShreckTableParticipant:
    row = get_table(session, table_id, ontology_id)
    if row.status != "active":
        raise HTTPException(410, "ShreckTable is archived")
    participant = ShreckTableParticipant(table_id=table_id, user_id=user_id, role=role,
                                         status="active", character_agent_id=character_agent_id)
    row.revision += 1
    session.add(participant)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "Participant is already associated with this table") from exc
    session.refresh(participant)
    return participant


def remove_participant(session: Session, table_id: str, ontology_id: int, user_id: str) -> None:
    row = get_table(session, table_id, ontology_id)
    if row.status != "active":
        raise HTTPException(410, "ShreckTable is archived")
    participant = session.scalar(select(ShreckTableParticipant).where(
        ShreckTableParticipant.table_id == table_id, ShreckTableParticipant.user_id == user_id))
    if not participant:
        raise HTTPException(404, "Participant not found")
    session.delete(participant)
    row.revision += 1
    session.commit()


def run_for_owner(session: Session, run_id: str, owner_id: str, ontology_id: int | None = None) -> GameGenerationRun:
    query = select(GameGenerationRun).where(GameGenerationRun.id == run_id, GameGenerationRun.owner_id == owner_id)
    if ontology_id is not None:
        query = query.where(GameGenerationRun.ontology_id == ontology_id)
    row = session.scalar(query)
    if not row:
        raise HTTPException(404, "Generation run not found")
    return row
