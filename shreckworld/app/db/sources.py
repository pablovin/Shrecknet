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
            hierarchy_columns = {col['name'] for col in inspect(connection).get_columns('source_hierarchies')}
            if 'source_type' not in hierarchy_columns:
                connection.execute(text("ALTER TABLE source_hierarchies ADD COLUMN source_type VARCHAR(16) NOT NULL DEFAULT 'explanatory'"))
            connection.execute(text("UPDATE source_hierarchies SET source_type='narrative' WHERE id IN ('rpg-campaign','campaign-sessions','novel')"))
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
                    section_types=['Rulebook'], source_type='explanatory', archived=False,
                ))
            # Keep the two supported starter templates active. Retire legacy
            # starters by appending no data; only the latest template row is shown.
            from app.models.sources import SourceHierarchy
            starters = [
                ("rpg-campaign", "RPG Campaign", "Narrative source hierarchy", ["Adventure", "Story Arc", "Story"], "narrative"),
                ("rulebook", "RPG Book", "Explanatory source hierarchy", ["Rulebook"], "explanatory"),
            ]
            for id, name, description, section_types, source_type in starters:
                current = connection.execute(text("SELECT version, name, section_types FROM source_hierarchies WHERE id=:id ORDER BY version DESC LIMIT 1"), {"id": id}).first()
                if current is None:
                    connection.execute(SourceHierarchy.__table__.insert().values(
                        id=id, version=1, name=name, description=description,
                        section_types=section_types, source_type=source_type, archived=False,
                    ))
                else:
                    import json
                    old_levels = current.section_types
                    if isinstance(old_levels, str):
                        old_levels = json.loads(old_levels)
                    if current.name != name or old_levels != section_types:
                        connection.execute(SourceHierarchy.__table__.insert().values(
                            id=id, version=current.version + 1, name=name,
                            description=description, section_types=section_types,
                            source_type=source_type, archived=False,
                        ))
                    else:
                        connection.execute(text("UPDATE source_hierarchies SET archived=FALSE, source_type=:kind WHERE id=:id AND version=:version"),
                                           {"kind": source_type, "id": id, "version": current.version})
            connection.execute(text("UPDATE source_hierarchies SET archived=TRUE WHERE id NOT IN ('rpg-campaign','rulebook') AND version=(SELECT MAX(h2.version) FROM source_hierarchies h2 WHERE h2.id=source_hierarchies.id)"))
            unresolved = connection.execute(text('SELECT COUNT(*) FROM shreckworld_library_items WHERE ontology_id IS NULL')).scalar_one()
            connection.commit()
            logger.info('Sources startup migration complete; %s legacy items retain unresolved ontology ownership', unresolved)
        except Exception:
            connection.rollback()
            raise
