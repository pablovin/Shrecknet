from __future__ import annotations

from sqlalchemy.orm import Session

from app import models as _models  # noqa: F401
from app.db.base import Base
from app.db.migrations import migrate_agents_table, migrate_deprecate_sql_ontology_instances
from app.db.session import get_engine, get_sessionmaker


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        migrate_agents_table(conn)
        migrate_deprecate_sql_ontology_instances(conn)

    sessionmaker = get_sessionmaker()
    with sessionmaker() as session:
        seed_initial_data(session)
        session.commit()


async def init_db_async() -> None:
    init_db()


def seed_initial_data(session: Session) -> None:
    del session
