from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PageVisitCreate(BaseModel):
    page_key: str = Field(..., min_length=1, max_length=255)


class PageVisitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    page_key: str
    user_id: int
    visited_at: datetime


class PageVisitUserRead(BaseModel):
    user_id: int
    username: str
    visited_at: datetime


class PageVisitStatsRead(BaseModel):
    page_key: str
    total_visits: int
    unique_users: int
    last_visited_at: datetime | None = None
    recent_visits: list[PageVisitUserRead]


class PageUserVisitSummaryRead(BaseModel):
    page_key: str
    visit_count: int
    first_visited_at: datetime
    last_visited_at: datetime
