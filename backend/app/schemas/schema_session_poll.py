from typing import List, Optional
from datetime import datetime, timezone
from sqlmodel import SQLModel
from pydantic import field_validator


class SessionPollCreate(SQLModel):
    proposed_times: List[datetime]
    timezone: str

    @field_validator("proposed_times")
    @classmethod
    def validate_proposed_times_timezone(cls, v: List[datetime]) -> List[datetime]:
        """Ensure all proposed_times are timezone-aware."""
        for dt in v:
            if dt.tzinfo is None:
                raise ValueError("All proposed_times must include timezone information")
        return v


class SessionPollVoteCreate(SQLModel):
    option_ids: List[int] = []


class SessionPollSelect(SQLModel):
    option_id: int


class SessionPollOptionRead(SQLModel):
    id: int
    proposed_time: datetime
    timezone: str
    votes: List[int] = []

    @field_validator("proposed_time", mode="before")
    @classmethod
    def convert_naive_to_utc(cls, v: datetime) -> datetime:
        """Convert naive datetime to UTC for backward compatibility."""
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class SessionPollRead(SQLModel):
    id: int
    session_id: int
    final_option_id: Optional[int] = None
    options: List[SessionPollOptionRead] = []
    created_at: datetime

    @field_validator("created_at", mode="before")
    @classmethod
    def convert_naive_to_utc(cls, v: datetime) -> datetime:
        """Convert naive datetime to UTC for backward compatibility."""
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


SessionPollCreate.model_rebuild()
SessionPollVoteCreate.model_rebuild()
SessionPollSelect.model_rebuild()
SessionPollOptionRead.model_rebuild()
SessionPollRead.model_rebuild()
