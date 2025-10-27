from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.notification import NotificationAuthorType, NotificationType


class NotificationBase(BaseModel):
    notification_type: NotificationType
    title: str = Field(..., max_length=255)
    description: str
    author_type: NotificationAuthorType
    author_id: str = Field(..., max_length=255)
    send_email: bool = False


class NotificationCreate(NotificationBase):
    user_id: int
    sent_at: datetime | None = None
    sent_date: datetime | None = None
    read: bool = False


class NotificationUpdate(BaseModel):
    notification_type: NotificationType | None = None
    title: str | None = Field(None, max_length=255)
    description: str | None = None
    author_type: NotificationAuthorType | None = None
    author_id: str | None = Field(None, max_length=255)
    sent_at: datetime | None = None
    sent_date: datetime | None = None
    read: bool | None = None
    send_email: bool | None = None


class NotificationRead(NotificationBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    sent_at: datetime
    read: bool
    sent_date: datetime | None = None
    updated_at: datetime


class NotificationReadState(BaseModel):
    read: bool = True


class NotificationUnreadCount(BaseModel):
    unread_count: int
