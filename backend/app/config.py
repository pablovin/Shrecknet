from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///:memory:"
    secret_key: str = "secret"
    debug: bool = False
    allowed_origins: str = "*"
    openai_api_key: str | None = None
    open_ai_model: str | None = None
    vector_db_path: str = "./data/vector_db"
    chat_history_dir: str = "./data/chat/{user_id}"
    vectordb_job_dir: str = "./data/vector_jobs"
    writer_job_dir: str = "./data/writer_jobs"
    specialist_job_dir: str = "./data/specialist_jobs"
    novelist_job_dir: str = "./data/novelist_jobs"
    library_job_dir: str = "./data/library_jobs"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/0"
    vector_db_url: str = "localhost"
    vector_db_port: str = "8001"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
