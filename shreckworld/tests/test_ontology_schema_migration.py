"""Migration preserves legacy integrity issues and rolls back new ones."""
import logging

import pytest
from sqlalchemy import create_engine, event, inspect

from app.db.ontology_items import migrate_ontology_items


def legacy_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/legacy.db")
    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE shreckworlds (id VARCHAR(36) PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE shreckworld_library_items (id VARCHAR(36) PRIMARY KEY, shreckworld_id VARCHAR(36) NOT NULL REFERENCES shreckworlds(id) ON DELETE CASCADE)")
        connection.exec_driver_sql("CREATE TABLE children (id TEXT PRIMARY KEY, item_id VARCHAR(36) REFERENCES shreckworld_library_items(id) ON DELETE CASCADE)")
        connection.exec_driver_sql("INSERT INTO shreckworlds VALUES ('world')")
        connection.exec_driver_sql("INSERT INTO shreckworld_library_items (rowid,id,shreckworld_id) VALUES (5,'book','world')")
        connection.exec_driver_sql("INSERT INTO children VALUES ('chunk','book')")
    return engine


def test_existing_orphans_and_sparse_rowids_are_preserved(tmp_path, caplog):
    engine = legacy_engine(tmp_path)
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        connection.exec_driver_sql("INSERT INTO children VALUES ('historical-job','missing-book')")
        connection.exec_driver_sql("INSERT INTO shreckworld_library_items (rowid,id,shreckworld_id) VALUES (9,'historical-book','missing-world')")
        connection.commit()
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        baseline = set(connection.exec_driver_sql("PRAGMA foreign_key_check").all())
    with caplog.at_level(logging.WARNING):
        migrate_ontology_items(engine)
        migrate_ontology_items(engine)
    with engine.connect() as connection:
        assert set(connection.exec_driver_sql("PRAGMA foreign_key_check").all()) == baseline
        assert connection.exec_driver_sql("SELECT rowid,id FROM shreckworld_library_items ORDER BY rowid").all() == [(5,'book'), (9,'historical-book')]
        assert connection.exec_driver_sql("SELECT count(*) FROM children").scalar_one() == 2
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    assert "2 pre-existing foreign-key violations" in caplog.text
    assert next(column for column in inspect(engine).get_columns("shreckworld_library_items") if column["name"] == "shreckworld_id")["nullable"]


def test_new_violation_rolls_back_rebuild_and_restores_foreign_keys(tmp_path):
    engine = legacy_engine(tmp_path)
    @event.listens_for(engine, "after_cursor_execute")
    def damage_rebuilt_table(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("ALTER TABLE ontology_items_migration RENAME"):
            cursor.execute("DELETE FROM shreckworld_library_items WHERE id='book'")
    with pytest.raises(RuntimeError, match="introduced new foreign-key violations"):
        migrate_ontology_items(engine)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT id FROM shreckworld_library_items").all() == [('book',)]
        assert connection.exec_driver_sql("SELECT id FROM children").all() == [('chunk',)]
        assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    assert not next(column for column in inspect(engine).get_columns("shreckworld_library_items") if column["name"] == "shreckworld_id")["nullable"]
    assert "ontology_items_migration" not in inspect(engine).get_table_names()
