from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.models.notification import NotificationType


class NotificationPreferenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    notification_type: NotificationType
    enabled: bool


class NotificationPreferenceUpdate(BaseModel):
    enabled: bool
