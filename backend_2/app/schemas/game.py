from __future__ import annotations

from datetime import datetime
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field


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


class GameSessionPollCreate(BaseModel):
    options: Sequence[GameSessionPollOptionCreate]


class GameSessionPollRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    is_finalized: bool
    finalized_option_id: int | None = None
    options: list[GameSessionPollOptionRead] = Field(default_factory=list)


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
