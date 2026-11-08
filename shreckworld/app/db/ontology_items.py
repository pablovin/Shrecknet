"""Add direct item ownership without deleting legacy sources or their children."""
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def migrate_ontology_items(engine: Engine) -> None:
    for table in ("shreckworld_library_items", "shreckworld_agent_jobs"):
        if table in inspect(engine).get_table_names():
            _migrate_direct_scope(engine, table)


def _migrate_direct_scope(engine: Engine, table: str) -> None:
    inspector = inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns(table)}
    with engine.begin() as connection:
        if "ontology_id" not in columns:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN ontology_id INTEGER"))
        connection.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_ontology_id ON {table}(ontology_id)"))
    if columns["shreckworld_id"]["nullable"]:
        return
    if engine.dialect.name != "sqlite":
        with engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE {table} ALTER COLUMN shreckworld_id DROP NOT NULL"))
        return
    # SQLite requires a table rebuild to relax NOT NULL. Foreign keys must be
    # disabled outside the transaction so dropping the old table cannot cascade.
    import re
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            with connection.begin():
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                schema = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).scalar_one()
                objects = list(connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type IN ('index', 'trigger') AND tbl_name=? AND sql IS NOT NULL ORDER BY type", (table,)).scalars())
                replacement = re.sub(r'^(CREATE TABLE\s+)(?:"' + table + r'"|' + table + r')', r'\1"ontology_items_migration"', schema, count=1, flags=re.I)
                replacement, count = re.subn(r'(\bshreckworld_id\s+VARCHAR\(36\))\s+NOT NULL', r'\1', replacement, count=1, flags=re.I)
                if count != 1:
                    raise RuntimeError("Unrecognized source schema; refusing to rebuild it")
                connection.exec_driver_sql(replacement)
                names = ", ".join('"' + name + '"' for name in [column["name"] for column in inspect(connection).get_columns(table)])
                connection.exec_driver_sql(f'INSERT INTO ontology_items_migration ({names}) SELECT {names} FROM {table}')
                connection.exec_driver_sql(f'DROP TABLE {table}')
                connection.exec_driver_sql(f'ALTER TABLE ontology_items_migration RENAME TO {table}')
                for sql in objects:
                    connection.exec_driver_sql(sql)
                if connection.exec_driver_sql("PRAGMA foreign_key_check").first():
                    raise RuntimeError("Source migration failed foreign-key verification")
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()
