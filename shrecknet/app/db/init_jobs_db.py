"""Initialize the jobs database."""

from sqlalchemy import Engine

from app.db.base import Base
from app.models.background_job import BackgroundJob  # noqa: F401 - model registration


def init_jobs_db(engine: Engine) -> None:
    """Create all tables in the jobs database."""
    Base.metadata.create_all(bind=engine)
