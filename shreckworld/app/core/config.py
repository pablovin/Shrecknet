from __future__ import annotations

from pathlib import Path
import os

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from shreckconfig.shreckworld import (
    DEFAULT_EMBEDDING_BACKEND, EmbeddingBackend, EmbeddingDevice, ParserDevice, ParserDeviceSetting, PdfParser,
    TableFormerMode, read_runtime_config, runtime_config_file,
)

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SHRECKWORLD_", extra="ignore", env_ignore_empty=True)

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
    embedding_device: EmbeddingDevice = "cpu"
    embedding_backend: EmbeddingBackend = DEFAULT_EMBEDDING_BACKEND
    embedding_artifacts_path: Path = Path("./models/embedding")
    embedding_revision: str | None = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    embedding_local_files_only: bool = True
    embedding_batch_size: int = Field(default=16, ge=1, le=128)
    embedding_threads: int = Field(default=4, ge=1, le=32)
    docling_artifacts_path: Path = Path("./models/docling")
    pdf_parser: PdfParser = "docling"
    parser_device: ParserDevice = "cpu"
    parser_heartbeat_seconds: float = Field(default=5, ge=0.1, le=60)
    parser_cache_enabled: bool = True
    parser_export_markdown: bool = False
    parser_threads: int = Field(default=4, ge=1, le=32)
    parser_layout_batch_size: int = Field(default=4, ge=1, le=64)
    parser_table_batch_size: int = Field(default=4, ge=1, le=64)
    parser_table_structure_enabled: bool = True
    parser_table_former_mode: TableFormerMode = "fast"
    mineru_api_url: str = "http://mineru:8000"
    mineru_api_key: str = ""
    mineru_tier: str = "standard"
    mineru_version: str = "4.1.7"
    mineru_timeout_seconds: int = Field(default=1800, ge=60, le=7200)
    mineru_poll_interval_seconds: float = Field(default=2.0, ge=0.25, le=30)
    reranker_model_id: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    reranker_enabled: bool = True
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "shreckworld_semantic_v2"
    shreckllm_base_url: str = "http://shreckllm:8110"
    # Legacy bootstrap defaults; the persisted model target takes precedence.
    synthesis_provider: str = Field(default="openrouter", min_length=1)
    synthesis_model: str = Field(default="deepseek/deepseek-v4.1-flash", min_length=1)
    synthesis_timeout_seconds: float = Field(default=600, ge=1, le=600)
    synthesis_poll_interval_seconds: float = Field(default=0.5, ge=0.1, le=5)
    synthesis_evidence_token_budget: int = Field(default=8000, ge=1000, le=32000)
    query_v3_planning_enabled: bool = True
    query_v3_coverage_review_enabled: bool = True
    query_v3_evidence_token_budget: int = Field(default=8000, ge=1000, le=32000)

    @model_validator(mode="after")
    def validate_embedding_device(self) -> "Settings":
        if self.embedding_backend != "torch" and self.embedding_device != "cpu":
            raise ValueError("ONNX and OpenVINO embedding backends require EMBEDDING_DEVICE=cpu")
        return self


def get_settings() -> Settings:
    shared = read_runtime_config(runtime_config_file())
    runtime_fields = {
        "embedding_backend", "embedding_device", "pdf_parser",
        "parser_table_structure_enabled", "parser_table_former_mode",
    }
    configured = {}
    if shared is not None:
        for field_name in runtime_fields:
            environment_key = f"SHRECKWORLD_{field_name.upper()}"
            if not os.environ.get(environment_key):
                configured[field_name] = getattr(shared, field_name)

    parser_device_setting: ParserDeviceSetting = "cpu"
    if shared is not None:
        parser_device_setting = shared.parser_device
    if os.environ.get("SHRECKWORLD_PARSER_DEVICE"):
        parser_device_setting = os.environ["SHRECKWORLD_PARSER_DEVICE"]  # validated below
    if parser_device_setting == "auto":
        try:
            import torch
            effective_parser_device: ParserDevice = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            effective_parser_device = "cpu"
    else:
        effective_parser_device = parser_device_setting
    configured["parser_device"] = effective_parser_device
    return Settings(**configured)
