from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GameMemberSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    full_name: str


class GameBase(BaseModel):
    name: str = Field(..., max_length=255)
    ontology_id: int


class GameCreate(GameBase):
    member_ids: list[int] = Field(default_factory=list)


class GameUpdate(BaseModel):
    name: str | None = Field(None, max_length=255)
    ontology_id: int | None = None
    member_ids: list[int] | None = None


class GameRead(GameBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
    members: list[GameMemberSummary] = Field(default_factory=list)


class GameSessionBase(BaseModel):
    title: str = Field(..., max_length=255)
    scheduled_date: datetime | None = None
    location: str | None = Field(None, max_length=255)
    summary: str | None = None


class GameSessionCreate(GameSessionBase):
    pass


class GameSessionUpdate(BaseModel):
    title: str | None = Field(None, max_length=255)
    scheduled_date: datetime | None = None
    location: str | None = Field(None, max_length=255)
    summary: str | None = None


class SessionPeriodicity(str, Enum):
    weekly = "weekly"
    biweekly = "biweekly"
    monthly = "monthly"


class GameSessionBulkCreate(BaseModel):
    title_prefix: str = Field(..., max_length=255)
    count: int | None = Field(None, ge=1)
    start_date: datetime | None = None
    periodicity: SessionPeriodicity | None = None
    dates: list[datetime] | None = None
    location: str | None = Field(None, max_length=255)
    summary: str | None = None

    @model_validator(mode="after")
    def validate_schedule(self) -> "GameSessionBulkCreate":
        if self.dates:
            if self.start_date is not None or self.periodicity is not None:
                raise ValueError(
                    "Provide either dates or start_date/periodicity, not both"
                )
            if self.count is not None and self.count != len(self.dates):
                raise ValueError("count must match the number of dates provided")
            return self

        if self.start_date is None or self.periodicity is None:
            raise ValueError(
                "start_date and periodicity are required when dates are not provided"
            )
        if self.count is None:
            raise ValueError("count is required when using periodicity")
        return self


class GameSessionAttendanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    attending: bool
    responded_at: datetime


class GameSessionPollOptionCreate(BaseModel):
    proposed_start: datetime


class GameSessionPollOptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    proposed_start: datetime
    vote_count: int = 0


class GameSessionPollVoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    created_at: datetime


class GameSessionPollOptionDetailRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    proposed_start: datetime
    vote_count: int = 0
    votes: list[GameSessionPollVoteRead] = Field(default_factory=list)


class GameSessionPollCreate(BaseModel):
    options: Sequence[GameSessionPollOptionCreate]


class GameSessionPollRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    is_finalized: bool
    finalized_option_id: int | None = None
    options: list[GameSessionPollOptionRead] = Field(default_factory=list)


class GameSessionPollDetailRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    is_finalized: bool
    finalized_option_id: int | None = None
    options: list[GameSessionPollOptionDetailRead] = Field(default_factory=list)


class GameSessionRead(GameSessionBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    game_id: int
    created_at: datetime
    updated_at: datetime
    attendance: list[GameSessionAttendanceRead] = Field(default_factory=list)
    polls: list[GameSessionPollRead] = Field(default_factory=list)


class AttendanceRequest(BaseModel):
    attending: bool


class PollVoteRequest(BaseModel):
    option_id: int


class PollFinalizeRequest(BaseModel):
    option_id: int
