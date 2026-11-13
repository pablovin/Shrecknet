"""Additive, automatic Sources upgrade. Never reads or mutates graph/vector stores."""
from __future__ import annotations
import logging
from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def migrate_sources(engine: Engine) -> None:
    # BEGIN IMMEDIATE serializes concurrent SQLite service starts before inspection.
    # PostgreSQL uses an advisory transaction lock for the same migration boundary.
    with engine.connect() as connection:
        if engine.dialect.name == 'sqlite':
            connection.exec_driver_sql('BEGIN IMMEDIATE')
        else:
            connection.begin()
            if engine.dialect.name == 'postgresql':
                connection.execute(text('SELECT pg_advisory_xact_lock(73921461)'))
        try:
            columns = {col['name'] for col in inspect(connection).get_columns('shreckworld_library_items')}
            for name, definition in {
                'source_type': "VARCHAR(16) NOT NULL DEFAULT 'explanatory'",
                'hierarchy_template_id': "VARCHAR(36) NOT NULL DEFAULT 'rulebook'",
                'hierarchy_template_version': 'INTEGER NOT NULL DEFAULT 1',
                'source_revision': 'INTEGER NOT NULL DEFAULT 1',
                'authored': 'BOOLEAN NOT NULL DEFAULT FALSE',
            }.items():
                if name not in columns:
                    connection.execute(text(f'ALTER TABLE shreckworld_library_items ADD COLUMN {name} {definition}'))
            # A stable seed is inserted once. User edits/archive versions are untouched.
            from app.models.sources import SourceHierarchy
            import json
            exists = connection.execute(text("SELECT 1 FROM source_hierarchies WHERE id='rulebook' AND version=1")).first()
            if not exists:
                connection.execute(SourceHierarchy.__table__.insert().values(
                    id='rulebook', version=1, name='Rulebook', description='Explanatory reference material',
                    section_types=['Rulebook', 'Chapter', 'Section'], archived=False,
                ))
            # Additional starter templates are independent of the Rulebook backfill.
            for id, name, description, section_types in [
                ("rpg-campaign", "RPG Campaign", "Adventures, story arcs and stories", ["Adventure", "Story Arc", "Story"]),
                ("campaign-sessions", "Campaign Adventures", "Adventure cards, story arcs and playable sessions", ["Adventure", "Story Arc", "Session"]),
                ("novel", "Novel", "Books, parts and chapters", ["Book", "Part", "Chapter"]),
                ("world-lore", "World Lore", "World setting, cultures and detailed topics", ["Topic", "Entry", "Section"]),
            ]:
                exists = connection.execute(
                    text("SELECT 1 FROM source_hierarchies WHERE id=:id AND version=1"), {"id": id},
                ).first()
                if not exists:
                    connection.execute(SourceHierarchy.__table__.insert().values(
                        id=id, version=1, name=name, description=description,
                        section_types=section_types, archived=False,
                    ))
            unresolved = connection.execute(text('SELECT COUNT(*) FROM shreckworld_library_items WHERE ontology_id IS NULL')).scalar_one()
            connection.commit()
            logger.info('Sources startup migration complete; %s legacy items retain unresolved ontology ownership', unresolved)
        except Exception:
            connection.rollback()
            raise
