"""Append-only operator trail for explicit source ownership migrations."""
from datetime import datetime
from uuid import uuid4
from sqlalchemy import DateTime, Integer, String, JSON, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class MigrationCenterAudit(Base):
    __tablename__ = "migration_center_audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    collection_id: Mapped[str] = mapped_column(String(36), nullable=False)
    target_ontology_id: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_items: Mapped[int] = mapped_column(Integer, nullable=False)
    preview_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
