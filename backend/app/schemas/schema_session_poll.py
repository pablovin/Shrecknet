from typing import List, Optional
from datetime import datetime
from sqlmodel import SQLModel


class SessionPollCreate(SQLModel):
    proposed_times: List[datetime]


class SessionPollVoteCreate(SQLModel):
    option_id: int


class SessionPollOptionRead(SQLModel):
    id: int
    proposed_time: datetime
    votes: List[int] = []


class SessionPollRead(SQLModel):
    id: int
    session_id: int
    final_option_id: Optional[int] = None
    options: List[SessionPollOptionRead] = []
    created_at: datetime


SessionPollCreate.model_rebuild()
SessionPollVoteCreate.model_rebuild()
SessionPollOptionRead.model_rebuild()
SessionPollRead.model_rebuild()
