from __future__ import annotations

from pathlib import Path

from pydantic import Field
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
    embedding_revision: str | None = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    embedding_local_files_only: bool = True
    embedding_batch_size: int = Field(default=16, ge=1, le=128)
    embedding_threads: int = Field(default=4, ge=1, le=32)
    docling_artifacts_path: Path = Path("./models/docling")
    parser_heartbeat_seconds: float = Field(default=5, ge=0.1, le=60)
    parser_cache_enabled: bool = True
    parser_export_markdown: bool = False
    parser_threads: int = Field(default=4, ge=1, le=32)
    parser_layout_batch_size: int = Field(default=4, ge=1, le=64)
    parser_table_batch_size: int = Field(default=4, ge=1, le=64)
    reranker_model_id: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    reranker_enabled: bool = True
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "shreckworld_semantic_v2"


def get_settings() -> Settings:
    return Settings()
