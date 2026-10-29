from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class BrowserLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identifier: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)
    remember_me: bool = False


class BrowserRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=3, max_length=150, pattern=r"^[a-zA-Z0-9_.-]+$")
    email: EmailStr
    password: str = Field(min_length=12, max_length=1024)
    timezone: str = Field(default="UTC", max_length=100)
    remember_me: bool = False

    @field_validator("full_name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Full name is required")
        return value.strip()

    @field_validator("timezone")
    @classmethod
    def timezone_exists(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value


class InstallationClaim(BrowserRegistration):
    claim_token: str | None = Field(default=None, min_length=32, max_length=256, repr=False)


class SessionUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    full_name: str
    email: str
    timezone: str
    role: str


class BrowserSessionResponse(BaseModel):
    user: SessionUser
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    access_expires_at: datetime
    session_expires_at: datetime
    idle_expires_at: datetime
    csrf_token: str


class BrowserRegistrationResponse(BaseModel):
    outcome: Literal["authenticated", "verification_required", "pending_approval"]
    session: BrowserSessionResponse | None = None
    verification_email_sent: bool | None = None


class PublicBrowserSettings(BaseModel):
    user_creation_mode: Literal["allowed", "moderated", "stopped"]
    email_verification_required: bool
    password_min_length: int
    persistent_days: int
    persistent_idle_days: int


class CsrfResponse(BaseModel):
    csrf_token: str


class AcceptedResponse(BaseModel):
    accepted: bool


class VerificationResponse(BaseModel):
    verified: bool


class BrowserError(BaseModel):
    code: str


class BrowserErrorResponse(BaseModel):
    detail: BrowserError | str
