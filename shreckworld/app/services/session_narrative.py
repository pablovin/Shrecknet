"""Publish reviewed ShreckTable material as a shared world narrative."""

from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.shrecktables import SessionNarrativeSource, TableAttachment
from app.models.sources import SourceHierarchy
from app.repositories.source_scope import OntologyScope
from app.schemas.sources import Paragraph, SourceCreate
from app.services.source_workspace import create_authored_source


def promote_session(
    db: Session, scope: OntologyScope, session_id: int, actor_id: str,
    title: str, narrative: str, attachment_id: int | None,
) -> SessionNarrativeSource:
    if db.get(SessionNarrativeSource, session_id) is not None:
        raise HTTPException(409, "This session already has a world narrative source")
    if attachment_id is not None and db.scalar(select(TableAttachment.id).where(
        TableAttachment.session_id == session_id, TableAttachment.id == attachment_id
    )) is None:
        raise HTTPException(404, "Attachment not found")
    hierarchy = db.scalar(select(SourceHierarchy).where(
        SourceHierarchy.id == "rpg-campaign", SourceHierarchy.archived.is_(False)
    ).order_by(SourceHierarchy.version.desc()).limit(1))
    if hierarchy is None:
        raise HTTPException(409, "The world narrative hierarchy is unavailable")

    promotion = SessionNarrativeSource(
        session_id=session_id, source_id=str(uuid4()),
        attachment_id=attachment_id, promoted_by=actor_id,
    )
    db.add(promotion)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "This session already has a world narrative source") from exc

    # The source workspace commits the source, organization and provenance
    # together and cleans up written Markdown if persistence fails.
    create_authored_source(
        db, scope, SourceCreate(
            title=title, source_type="narrative",
            hierarchy_template_id=hierarchy.id, hierarchy_template_version=hierarchy.version,
        ),
        source_id=promotion.source_id,
        paragraphs=[Paragraph(id=str(uuid4()), text=narrative)],
    )
    return promotion
