from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.models.user import UserApprovalStatus, UserRole
from app.core.config_store import UserCreationMode


class UserBase(BaseModel):
    username: str
    full_name: str
    email: EmailStr
    timezone: str
    role: UserRole = UserRole.PLAYER
    avatar_url: str | None = None
    approval_status: UserApprovalStatus = UserApprovalStatus.APPROVED
    approval_decided_by_user_id: int | None = None
    approval_decided_at: datetime | None = None
    entity_ids: list[int] = Field(default_factory=list)


class UserCreate(UserBase):
    password: str = Field(min_length=6)

    @model_validator(mode="after")
    def ensure_entity_ids_unique(self) -> "UserCreate":
        if len(self.entity_ids) != len(set(self.entity_ids)):
            raise ValueError("entity_ids must be unique")
        return self


class UserUpdate(BaseModel):
    username: str | None = None
    password: str | None = Field(default=None, min_length=6)
    full_name: str | None = None
    email: EmailStr | None = None
    timezone: str | None = None
    role: UserRole | None = None
    avatar_url: str | None = None
    entity_ids: list[int] | None = None

    @model_validator(mode="after")
    def ensure_entity_ids_unique(self) -> "UserUpdate":
        if self.entity_ids is not None and len(self.entity_ids) != len(
            set(self.entity_ids)
        ):
            raise ValueError("entity_ids must be unique")
        return self


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    full_name: str
    email: EmailStr
    timezone: str
    role: UserRole
    avatar_url: str | None = None
    email_verified_at: datetime | None = None
    verification_email_sent: bool | None = None
    entity_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def extract_entity_ids(self) -> "UserRead":
        entities = getattr(self, "entities", None)
        if entities is not None:
            object.__setattr__(
                self,
                "entity_ids",
                [entity.id for entity in entities],
            )
        return self


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    sub: str | None = None
    exp: datetime | None = None


class UserAvailabilityResponse(BaseModel):
    username_available: bool | None = None
    email_available: bool | None = None


class UserBootstrapStatus(BaseModel):
    has_users: bool


class PublicRegistrationConfig(BaseModel):
    """The only registration setting safe to disclose before authentication."""

    user_creation_mode: UserCreationMode
    email_verification_required: bool = False


class EmailVerificationRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class EmailVerificationResendRequest(BaseModel):
    email: EmailStr


class ServiceEmailSendRequest(BaseModel):
    user_id: int = Field(gt=0)
    subject: str = Field(min_length=1, max_length=998)
    message: str = Field(min_length=1, max_length=100_000)
