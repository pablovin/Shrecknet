from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class World(Base):
    __tablename__ = "worlds"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)

    description: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(String(512))
    rpg_system: Mapped[str | None] = mapped_column(String(255))
    rpg_edition: Mapped[str | None] = mapped_column(String(100))
    rpg_subversion: Mapped[str | None] = mapped_column(String(100))

    ontologies = relationship("Ontology", back_populates="world", cascade="all, delete-orphan")
