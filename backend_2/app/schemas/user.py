from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.models.user import UserRole


class UserBase(BaseModel):
    username: str
    full_name: str
    email: EmailStr
    timezone: str
    role: UserRole = UserRole.PLAYER
    avatar_url: str | None = None
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
    entity_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def extract_entity_ids(self) -> "UserRead":
        entities = getattr(self, "entities", None)
        if entities is not None:
            object.__setattr__(
                self, "entity_ids", [entity.id for entity in entities],
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
