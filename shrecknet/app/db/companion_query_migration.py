"""Idempotent additive Companion v2 migration, including durable message ordering.

Run before starting workers. SQLite rebuilds only the message table; no table
references its primary key. Other databases replace the old unique constraint.
"""
from sqlalchemy import inspect, text


def migrate_companion_query(conn):
    inspector = inspect(conn)
    additions = {
        'character_companion_chats': {'resolved_ontology': 'JSON', 'pending_clarification': 'JSON', 'scope_override': 'BOOLEAN NOT NULL DEFAULT FALSE'},
        'character_companion_turns': {'execution_context': 'JSON', 'stage': 'VARCHAR(50)'},
    }
    for table, fields in additions.items():
        if not inspector.has_table(table):
            continue
        columns = {c['name'] for c in inspect(conn).get_columns(table)}
        for name, kind in fields.items():
            if name not in columns:
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {kind}'))
    table = 'character_companion_messages'
    if not inspector.has_table(table):
        return
    columns = {c['name'] for c in inspect(conn).get_columns(table)}
    if 'sequence' in columns:
        return
    if conn.dialect.name == 'sqlite':
        # No inbound foreign keys target messages. Preserve IDs, timestamps and
        # manifests exactly; keep all work inside the caller's transaction.
        # A crash before the first INSERT can leave this empty staging table on
        # pysqlite's legacy DDL autocommit mode. The unchanged old table remains
        # authoritative until INSERT/DROP/RENAME commit together.
        conn.execute(text('DROP TABLE IF EXISTS character_companion_messages_v2'))
        conn.execute(text('''CREATE TABLE character_companion_messages_v2 (
            id VARCHAR(36) PRIMARY KEY, turn_id VARCHAR(36) NOT NULL REFERENCES character_companion_turns(id) ON DELETE CASCADE,
            chat_id VARCHAR(36) NOT NULL REFERENCES character_companion_chats(id) ON DELETE CASCADE,
            role VARCHAR(20) NOT NULL, kind VARCHAR(20) NOT NULL DEFAULT 'final', sequence INTEGER NOT NULL DEFAULT 0,
            content TEXT NOT NULL, manifest JSON NOT NULL, committed BOOLEAN NOT NULL,
            created_at DATETIME NOT NULL, UNIQUE(turn_id, role, sequence))'''))
        conn.execute(text('''INSERT INTO character_companion_messages_v2
            SELECT id, turn_id, chat_id, role, CASE WHEN role='user' THEN 'user' WHEN role='interim' THEN 'commentary' ELSE 'final' END,
                   0, content, manifest, committed, created_at FROM character_companion_messages'''))
        conn.execute(text('DROP TABLE character_companion_messages'))
        conn.execute(text('ALTER TABLE character_companion_messages_v2 RENAME TO character_companion_messages'))
        for field in ('turn_id', 'chat_id'):
            conn.execute(text(f'CREATE INDEX ix_character_companion_messages_{field} ON {table} ({field})'))
    else:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN kind VARCHAR(20) NOT NULL DEFAULT 'final'"))
        conn.execute(text(f'ALTER TABLE {table} ADD COLUMN sequence INTEGER NOT NULL DEFAULT 0'))
        conn.execute(text(f"UPDATE {table} SET kind='user' WHERE role='user'"))
        conn.execute(text(f"UPDATE {table} SET kind='commentary' WHERE role='interim'"))
        for constraint in inspector.get_unique_constraints(table):
            if set(constraint['column_names']) == {'turn_id', 'role'}:
                name = conn.dialect.identifier_preparer.quote(constraint['name'])
                conn.execute(text(f'ALTER TABLE {table} DROP CONSTRAINT {name}'))
        conn.execute(text(f'ALTER TABLE {table} ADD CONSTRAINT uq_companion_message_sequence UNIQUE (turn_id, role, sequence)'))
