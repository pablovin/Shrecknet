"""Private browser-session configuration, intentionally outside public app config."""
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from cryptography.fernet import Fernet


class BrowserAuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SHRECKNET_BROWSER_", extra="ignore")

    enabled: bool = False
    secret: str = ""
    allowed_origins: list[str] = Field(default_factory=list)
    secure_cookies: bool = True
    access_minutes: int = Field(default=10, ge=1, le=30)
    persistent_days: int = Field(default=30, ge=1, le=90)
    persistent_idle_days: int = Field(default=7, ge=1, le=30)
    session_hours: int = Field(default=12, ge=1, le=24)
    session_idle_hours: int = Field(default=2, ge=1, le=12)

    @model_validator(mode="after")
    def validate_enabled(self):
        if self.enabled:
            Fernet(self.secret.encode())
            if not self.allowed_origins or any(origin.endswith("/") for origin in self.allowed_origins):
                raise ValueError("Browser auth requires exact origins without trailing slashes")
            from urllib.parse import urlsplit
            for origin in self.allowed_origins:
                url = urlsplit(origin)
                if not url.netloc or url.path or url.query or url.fragment or url.username:
                    raise ValueError("Invalid browser authentication origin")
                if url.scheme != "https" and (self.secure_cookies or url.hostname not in {"localhost", "127.0.0.1", "::1"}):
                    raise ValueError("HTTP cookies are restricted to explicit loopback development")
        return self

    @property
    def refresh_cookie(self) -> str:
        return "__Host-shrecknet_refresh" if self.secure_cookies else "shrecknet_dev_refresh"

    @property
    def csrf_cookie(self) -> str:
        return "__Host-shrecknet_csrf" if self.secure_cookies else "shrecknet_dev_csrf"


@lru_cache
def get_browser_auth_settings() -> BrowserAuthSettings:
    return BrowserAuthSettings()
