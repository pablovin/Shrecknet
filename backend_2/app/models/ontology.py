from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.sql import expression
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.user import user_entities

if TYPE_CHECKING:  # pragma: no cover
    from app.models.library import LibraryItem
    from app.models.note import Note
    from app.models.user import User


class AuthorType(str, Enum):
    HUMAN = "human"
    AGENT = "agent"


class Cardinality(str, Enum):
    ONE = "one"
    MANY = "many"


class PropertyDataType(str, Enum):
    TEXT = "text"
    NUMBER = "number"
    IMAGE = "image"
    DATE = "date"
    FOUNDRY_JSON = "foundry_character_sheet_json"
    PDF_LINK = "pdf_link"
    WEBSITE_LINK = "website_link"
    YOUTUBE_LINK = "youtube_link"
    SUNO_LINK = "suno_link"
    SPOTIFY_LINK = "spotify_link"


class Ontology(Base):
    __tablename__ = "ontologies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    display_on_world: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=expression.true(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    entities: Mapped[List[OntologyEntity]] = relationship(
        "OntologyEntity",
        back_populates="ontology",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    library_items: Mapped[List["LibraryItem"]] = relationship(
        "LibraryItem",
        back_populates="ontology",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    notes: Mapped[List["Note"]] = relationship(
        "Note",
        back_populates="ontology",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class OntologyEntity(Base):
    __tablename__ = "ontology_entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    ontology_id: Mapped[int] = mapped_column(
        ForeignKey("ontologies.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    auto_generatable: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    author_type: Mapped[AuthorType] = mapped_column(SqlEnum(AuthorType), nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    agent_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    ontology: Mapped[Ontology] = relationship("Ontology", back_populates="entities")
    players: Mapped[List["User"]] = relationship(
        "User", secondary=user_entities, back_populates="entities"
    )
    properties: Mapped[List[OntologyProperty]] = relationship(
        "OntologyProperty",
        back_populates="entity",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    relationships: Mapped[List[OntologyRelationship]] = relationship(
        "OntologyRelationship",
        back_populates="entity",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="OntologyRelationship.entity_id",
    )


class OntologyProperty(Base):
    __tablename__ = "entity_properties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("ontology_entities.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    cardinality: Mapped[Cardinality] = mapped_column(
        SqlEnum(Cardinality), nullable=False
    )
    data_type: Mapped[PropertyDataType] = mapped_column(
        SqlEnum(PropertyDataType), nullable=False
    )
    auto_generatable: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    author_type: Mapped[AuthorType] = mapped_column(SqlEnum(AuthorType), nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    agent_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    entity: Mapped[OntologyEntity] = relationship(
        "OntologyEntity", back_populates="properties"
    )


class OntologyRelationship(Base):
    __tablename__ = "entity_relationships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("ontology_entities.id", ondelete="CASCADE")
    )
    destiny_entity_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_entities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    bi_directional: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_generatable: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    author_type: Mapped[AuthorType] = mapped_column(SqlEnum(AuthorType), nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    agent_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    entity: Mapped[OntologyEntity] = relationship(
        "OntologyEntity", back_populates="relationships", foreign_keys=[entity_id],
    )
    destiny_entity: Mapped[Optional[OntologyEntity]] = relationship(
        "OntologyEntity", foreign_keys=[destiny_entity_id]
    )
