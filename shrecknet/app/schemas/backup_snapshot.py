from __future__ import annotations

from pydantic import BaseModel, Field


class SnapshotCreateRequest(BaseModel):
    categories: list[str] = Field(min_length=1)
    incremental: bool = True
    label: str | None = Field(default=None, max_length=120)


class SnapshotRestoreRequest(BaseModel):
    categories: list[str] = Field(min_length=1)
    safety_snapshot: bool = True
    keep_only_admin_in_rpg: bool = True
