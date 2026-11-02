"""Preserve SQL history and legacy transcript paths when retiring Elder Agents."""
import re
from sqlalchemy import inspect, text


def migrate_site_elder_chats(engine) -> None:
    # SQLite foreign-key disabling must happen before the table-copy transaction.
    # Dropping a parent with FKs enabled would cascade-delete SQL history.
    with engine.connect() as conn:
        if "elder_chats" not in inspect(conn).get_table_names():
            return
        previous = conn.exec_driver_sql("PRAGMA foreign_keys").scalar()
        conn.commit()
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.commit()
        try:
            with conn.begin():
                columns = {
                    c["name"]: c for c in inspect(conn).get_columns("elder_chats")
                }
                for name, sql_type in (
                    ("scope_mode", "VARCHAR(16)"),
                    ("ontology_ids", "JSON"),
                    ("legacy_storage_agent_id", "VARCHAR(36)"),
                ):
                    if name not in columns:
                        conn.execute(
                            text(
                                f"ALTER TABLE elder_chats ADD COLUMN {name} {sql_type}"
                            )
                        )
                conn.execute(
                    text(
                        "UPDATE elder_chats SET legacy_storage_agent_id=agent_id WHERE scope_mode IS NULL AND legacy_storage_agent_id IS NULL"
                    )
                )
                fks = inspect(conn).get_foreign_keys("elder_chats")
                cascade = any(
                    f["constrained_columns"] == ["agent_id"]
                    and f.get("options", {}).get("ondelete", "").upper() == "CASCADE"
                    for f in fks
                )
                if not columns["agent_id"]["nullable"] or cascade:
                    ddl = conn.execute(
                        text(
                            "SELECT sql FROM sqlite_master WHERE name='elder_chats' AND type='table'"
                        )
                    ).scalar_one()
                    indexes = (
                        conn.execute(
                            text(
                                "SELECT sql FROM sqlite_master WHERE tbl_name='elder_chats' AND type='index' AND sql IS NOT NULL"
                            )
                        )
                        .scalars()
                        .all()
                    )
                    ddl = ddl.replace("elder_chats", "elder_chats__site_migration", 1)
                    ddl = re.sub(
                        r"(agent_id\s+VARCHAR\(36\))\s+NOT NULL", r"\1", ddl, flags=re.I
                    )
                    ddl = re.sub(
                        r"(REFERENCES\s+agents\s*\(id\)\s+ON DELETE)\s+CASCADE",
                        r"\1 SET NULL",
                        ddl,
                        flags=re.I,
                    )
                    conn.execute(text(ddl))
                    names = ", ".join(
                        '"' + c["name"] + '"'
                        for c in inspect(conn).get_columns("elder_chats")
                    )
                    conn.execute(
                        text(
                            f"INSERT INTO elder_chats__site_migration ({names}) SELECT {names} FROM elder_chats"
                        )
                    )
                    conn.execute(text("DROP TABLE elder_chats"))
                    conn.execute(
                        text(
                            "ALTER TABLE elder_chats__site_migration RENAME TO elder_chats"
                        )
                    )
                    for index in indexes:
                        conn.execute(text(index))
        finally:
            conn.exec_driver_sql(f"PRAGMA foreign_keys={int(previous)}")
            conn.commit()
