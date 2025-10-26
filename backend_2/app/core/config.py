from functools import lru_cache
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BACKEND_2_", env_file=".env", env_file_encoding="utf-8"
    )

    app_name: str = "backend_2"
    database_url: str = "sqlite+aiosqlite:///./backend_2.db"
    debug: bool = False
    secret_key: str = "change-me"
    access_token_expire_minutes: int = 30
    jwt_algorithm: str = "HS256"
    cors_allow_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost",
            "http://localhost:3000",
            "https://lovableproject.com",
            "https://shrecknet.club",
            "https://c56f54ad-02b8-428f-9e87-43f81dab0914.lovableproject.com",
        ]
    )
    cors_allow_origin_regex: str | None = r"https://.*\\.lovableproject\\.com"
    cors_allow_credentials: bool = True
    cors_allow_methods: list[str] = Field(default_factory=lambda: ["*"])
    cors_allow_headers: list[str] = Field(default_factory=lambda: ["*"])
    cors_max_age: int = 3600


class AppConfig(BaseModel):
    app_name: str
    debug: bool


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_app_config(settings: Settings | None = None) -> AppConfig:
    settings = settings or get_settings()
    return AppConfig(app_name=settings.app_name, debug=settings.debug)
