from __future__ import annotations

from sqlalchemy.orm import Session

from app import models as _models  # noqa: F401
from app.db.base import Base
from app.db.migrations import (
    migrate_agents_table,
    migrate_foundry_default_world_column,
    migrate_world_workshop_runs,
    migrate_novelist_agent_preferences,
    migrate_remove_legacy_character_agents,
    migrate_scene_perspective_audit_types,
    migrate_deprecate_sql_ontology_instances,
    migrate_ontology_rpg_system_column,
    migrate_personal_companion_agents_table,
    migrate_user_approval_columns,
    migrate_user_email_verification_columns,
    migrate_existing_users_email_verified,
    migrate_character_embodiment_timeline_column,
)
from app.db.companion_query_migration import migrate_companion_query
from app.db.elder_chat_migration import migrate_site_elder_chats
from app.db.session import get_engine, get_sessionmaker


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    migrate_site_elder_chats(engine)
    with engine.begin() as conn:
        migrate_companion_query(conn)
        migrate_foundry_default_world_column(conn)
        migrate_agents_table(conn)
        migrate_novelist_agent_preferences(conn)
        migrate_world_workshop_runs(conn)
        migrate_deprecate_sql_ontology_instances(conn)
        migrate_personal_companion_agents_table(conn)
        migrate_remove_legacy_character_agents(conn)
        migrate_scene_perspective_audit_types(conn)
        migrate_ontology_rpg_system_column(conn)
        migrate_user_approval_columns(conn)
        migrate_user_email_verification_columns(conn)
        migrate_existing_users_email_verified(conn)
        migrate_character_embodiment_timeline_column(conn)

    sessionmaker = get_sessionmaker()
    with sessionmaker() as session:
        seed_initial_data(session)
        session.commit()


async def init_db_async() -> None:
    init_db()


def seed_initial_data(session: Session) -> None:
    del session
