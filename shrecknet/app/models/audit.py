from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import (
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditAction(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class AuditActorType(str, Enum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class AuditEntityType(str, Enum):
    USER = "user"
    ONTOLOGY = "ontology"
    ONTOLOGY_ENTITY = "ontology_entity"
    ONTOLOGY_PROPERTY = "ontology_property"
    ONTOLOGY_RELATIONSHIP = "ontology_relationship"
    NOTIFICATION = "notification"
    CHARACTER_AGENT = "character_agent"
    CHARACTER_ASPECT = "character_aspect"
    CHARACTER_GOAL = "character_goal"
    CHARACTER_ASPECT_ASSIGNMENT = "character_aspect_assignment"
    CHARACTER_GOAL_PURSUIT = "character_goal_pursuit"
    SCENE_PERSPECTIVE = "scene_perspective"
    EMOTIONAL_INTERPRETATION = "emotional_interpretation"
    CHARACTER_BELIEF = "character_belief"
    CHARACTER_IMPACT = "character_impact"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    actor_type: Mapped[AuditActorType] = mapped_column(
        SqlEnum(AuditActorType), nullable=False, default=AuditActorType.USER
    )
    actor_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_agent_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, index=True
    )
    action: Mapped[AuditAction] = mapped_column(SqlEnum(AuditAction), nullable=False)
    entity_type: Mapped[AuditEntityType] = mapped_column(
        SqlEnum(AuditEntityType), nullable=False, index=True
    )
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    context: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
