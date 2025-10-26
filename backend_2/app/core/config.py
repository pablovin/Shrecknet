from functools import lru_cache
from pydantic import BaseModel
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


class AppConfig(BaseModel):
    app_name: str
    debug: bool


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_app_config(settings: Settings | None = None) -> AppConfig:
    settings = settings or get_settings()
    return AppConfig(app_name=settings.app_name, debug=settings.debug)
