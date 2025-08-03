from typing import Optional, List
from datetime import datetime, timezone
from sqlmodel import SQLModel, Field, Relationship


class Session(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    table_id: int = Field(foreign_key="table.id")
    name: str
    scheduled_time: Optional[datetime] = Field(default=None, nullable=True)
    summary: Optional[str] = None
    location: Optional[str] = None
    timezone: str = Field(default="UTC")
    created_by: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    attendances: List["SessionAttendance"] = Relationship(back_populates="session")
    pages: List["SessionPage"] = Relationship(back_populates="session")
    poll: Optional["SessionPoll"] = Relationship(back_populates="session")


class SessionAttendance(SQLModel, table=True):
    session_id: int = Field(foreign_key="session.id", primary_key=True)
    user_id: int = Field(foreign_key="user.id", primary_key=True)
    attending: bool = Field(default=True)

    session: "Session" = Relationship(back_populates="attendances")


class SessionPage(SQLModel, table=True):
    session_id: int = Field(foreign_key="session.id", primary_key=True)
    page_id: int = Field(foreign_key="page.id", primary_key=True)

    session: "Session" = Relationship(back_populates="pages")


class SessionPoll(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id")
    final_option_id: Optional[int] = Field(
        default=None, foreign_key="sessionpolloption.id"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    session: "Session" = Relationship(back_populates="poll")
    # Explicitly specify the foreign key used for the options relationship. Without
    # this SQLModel/SQLAlchemy cannot determine which foreign key path to use when
    # joining SessionPoll and SessionPollOption because `final_option_id` also
    # references `SessionPollOption`. This leads to the "multiple foreign key
    # paths" error on application startup.  By pointing the relationship to
    # `SessionPollOption.poll_id` we make the join unambiguous.
    options: List["SessionPollOption"] = Relationship(
        back_populates="poll",
        sa_relationship_kwargs={"foreign_keys": "SessionPollOption.poll_id"},
    )
    votes: List["SessionPollVote"] = Relationship(
        back_populates="poll",
        sa_relationship_kwargs={"foreign_keys": "SessionPollVote.poll_id"},
    )


class SessionPollOption(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    poll_id: int = Field(foreign_key="sessionpoll.id")
    proposed_time: datetime

    poll: "SessionPoll" = Relationship(
        back_populates="options",
        sa_relationship_kwargs={"foreign_keys": "SessionPollOption.poll_id"},
    )
    votes: List["SessionPollVote"] = Relationship(
        back_populates="option",
        sa_relationship_kwargs={"foreign_keys": "SessionPollVote.option_id"},
    )


class SessionPollVote(SQLModel, table=True):
    poll_id: int = Field(foreign_key="sessionpoll.id", primary_key=True)
    option_id: int = Field(foreign_key="sessionpolloption.id", primary_key=True)
    user_id: int = Field(foreign_key="user.id", primary_key=True)

    poll: "SessionPoll" = Relationship(
        back_populates="votes",
        sa_relationship_kwargs={"foreign_keys": "SessionPollVote.poll_id"},
    )
    option: "SessionPollOption" = Relationship(
        back_populates="votes",
        sa_relationship_kwargs={"foreign_keys": "SessionPollVote.option_id"},
    )
