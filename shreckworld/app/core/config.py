from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SHRECKWORLD_", extra="ignore")

    app_name: str = "ShreckWorld"
    database_url: str = "sqlite:///./databases/shreckworld.db"
    media_root: Path = Path("./media")
    media_base_url: str = "/media"
    max_pdf_bytes: int = 300 * 1024 * 1024
    max_document_bytes: int = 300 * 1024 * 1024
    # ShreckWorld trusts Shrecknet-issued tokens; it has no local login.
    shrecknet_jwks_url: str = "http://shrecknet_api:8110/auth/jwks"
    shrecknet_jwt_issuer: str = "shrecknet"
    shrecknet_jwt_audience: str = "shreckrpg"
    admin_token: str = ""
    celery_broker_url: str = "redis://shreckworld_redis:6379/0"
    celery_result_backend: str = "redis://shreckworld_redis:6379/1"
    celery_task_always_eager: bool = False
    embedding_model_id: str = "intfloat/multilingual-e5-small"
    embedding_dimension: int = 384
    embedding_device: str = "cpu"
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "shreckworld_chunks_v1"


def get_settings() -> Settings:
    return Settings()
