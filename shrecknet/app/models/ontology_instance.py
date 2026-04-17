from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OntologyInstance(Base):
    __tablename__ = "ontology_instances"

    instance_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ontology_id: Mapped[int] = mapped_column(ForeignKey("ontologies.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    slug_alias: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class OntologyInstanceTimelineEvent(Base):
    __tablename__ = "ontology_instance_timeline_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    instance_id: Mapped[str] = mapped_column(
        ForeignKey("ontology_instances.instance_id", ondelete="CASCADE"),
        index=True,
    )
    ontology_id: Mapped[int] = mapped_column(ForeignKey("ontologies.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    source_entity_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    involves_entity_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    relations_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class FavoriteOntologyInstance(Base):
    __tablename__ = "favorite_ontology_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    instance_id: Mapped[str] = mapped_column(
        ForeignKey("ontology_instances.instance_id", ondelete="CASCADE"),
        index=True,
    )
    ontology_id: Mapped[int] = mapped_column(ForeignKey("ontologies.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("user_id", "instance_id", name="uq_favorite_instance_user"),)
