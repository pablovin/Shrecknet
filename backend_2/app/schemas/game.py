from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator


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

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def convert_naive_to_utc(cls, v: datetime) -> datetime:
        """Convert naive datetime to UTC for backward compatibility."""
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class GameSessionBase(BaseModel):
    title: str = Field(..., max_length=255)
    scheduled_date: datetime | None = None
    location: str | None = Field(None, max_length=255)
    summary: str | None = None

    @field_validator("scheduled_date")
    @classmethod
    def validate_scheduled_date_timezone(cls, v: datetime | None) -> datetime | None:
        """Ensure scheduled_date is timezone-aware if provided."""
        if v is not None and v.tzinfo is None:
            raise ValueError("scheduled_date must include timezone information")
        return v


class GameSessionCreate(GameSessionBase):
    pass


class GameSessionUpdate(BaseModel):
    title: str | None = Field(None, max_length=255)
    scheduled_date: datetime | None = None
    location: str | None = Field(None, max_length=255)
    summary: str | None = None

    @field_validator("scheduled_date")
    @classmethod
    def validate_scheduled_date_timezone(cls, v: datetime | None) -> datetime | None:
        """Ensure scheduled_date is timezone-aware if provided."""
        if v is not None and v.tzinfo is None:
            raise ValueError("scheduled_date must include timezone information")
        return v


class GameSessionAttendanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    attending: bool
    responded_at: datetime

    @field_validator("responded_at", mode="before")
    @classmethod
    def convert_naive_to_utc(cls, v: datetime) -> datetime:
        """Convert naive datetime to UTC for backward compatibility."""
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class GameSessionPollOptionCreate(BaseModel):
    proposed_start: datetime

    @field_validator("proposed_start")
    @classmethod
    def validate_proposed_start_timezone(cls, v: datetime) -> datetime:
        """Ensure proposed_start is timezone-aware."""
        if v.tzinfo is None:
            raise ValueError("proposed_start must include timezone information")
        return v


class GameSessionPollOptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    proposed_start: datetime
    vote_count: int = 0

    @field_validator("proposed_start", mode="before")
    @classmethod
    def convert_naive_to_utc(cls, v: datetime) -> datetime:
        """Convert naive datetime to UTC for backward compatibility."""
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class GameSessionPollVoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    created_at: datetime

    @field_validator("created_at", mode="before")
    @classmethod
    def convert_naive_to_utc(cls, v: datetime) -> datetime:
        """Convert naive datetime to UTC for backward compatibility."""
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class GameSessionPollOptionDetailRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    proposed_start: datetime
    vote_count: int = 0
    votes: list[GameSessionPollVoteRead] = Field(default_factory=list)

    @field_validator("proposed_start", mode="before")
    @classmethod
    def convert_naive_to_utc(cls, v: datetime) -> datetime:
        """Convert naive datetime to UTC for backward compatibility."""
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class GameSessionPollCreate(BaseModel):
    options: Sequence[GameSessionPollOptionCreate]


class GameSessionPollRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    is_finalized: bool
    finalized_option_id: int | None = None
    options: list[GameSessionPollOptionRead] = Field(default_factory=list)

    @field_validator("created_at", mode="before")
    @classmethod
    def convert_naive_to_utc(cls, v: datetime) -> datetime:
        """Convert naive datetime to UTC for backward compatibility."""
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class GameSessionPollDetailRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    is_finalized: bool
    finalized_option_id: int | None = None
    options: list[GameSessionPollOptionDetailRead] = Field(default_factory=list)

    @field_validator("created_at", mode="before")
    @classmethod
    def convert_naive_to_utc(cls, v: datetime) -> datetime:
        """Convert naive datetime to UTC for backward compatibility."""
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class GameSessionRead(GameSessionBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    game_id: int
    created_at: datetime
    updated_at: datetime
    attendance: list[GameSessionAttendanceRead] = Field(default_factory=list)
    polls: list[GameSessionPollRead] = Field(default_factory=list)

    @field_validator("created_at", "updated_at", "scheduled_date", mode="before")
    @classmethod
    def convert_naive_to_utc(cls, v: datetime | None) -> datetime | None:
        """Convert naive datetime to UTC for backward compatibility."""
        if v is not None and isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class AttendanceRequest(BaseModel):
    attending: bool


class PollVoteRequest(BaseModel):
    option_id: int


class PollFinalizeRequest(BaseModel):
    option_id: int
