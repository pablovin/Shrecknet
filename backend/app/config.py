from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    # Provide sane defaults so tests can run without a .env file
    database_url: str = "sqlite+aiosqlite:///./dev.db"
    secret_key: str = "dev-secret"
    debug: bool = False
    allowed_origins: str = "*"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()