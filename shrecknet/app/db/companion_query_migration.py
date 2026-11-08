"""Automatic, idempotent Companion schema upgrade invoked by init_db at startup.

Preserve message IDs/content/timestamps and add scope/progress plus durable
commentary ordering. SQLite rebuilds only messages (no inbound references).
Partial upgrades are repaired, including an old uniqueness constraint surviving
an earlier column addition. Workers wait for API readiness in Compose.
"""
from sqlalchemy import inspect, text


def migrate_companion_query(conn):
    additions = {
        'character_companion_chats': {'resolved_ontology': 'JSON', 'pending_clarification': 'JSON', 'scope_override': 'BOOLEAN NOT NULL DEFAULT FALSE'},
        'character_companion_turns': {'execution_context': 'JSON', 'stage': 'VARCHAR(50)'},
    }
    for table, fields in additions.items():
        if not inspect(conn).has_table(table):
            continue
        columns = {c['name'] for c in inspect(conn).get_columns(table)}
        for name, kind in fields.items():
            if name not in columns:
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {kind}'))
    table = 'character_companion_messages'
    if not inspect(conn).has_table(table):
        return
    columns = {c['name'] for c in inspect(conn).get_columns(table)}
    constraints = inspect(conn).get_unique_constraints(table)
    old = [c for c in constraints if set(c['column_names']) == {'turn_id', 'role'}]
    needs_upgrade = not {'kind', 'sequence'} <= columns or bool(old)
    if needs_upgrade and conn.dialect.name == 'sqlite':
        # A crash before INSERT may leave empty staging DDL. The original table
        # remains authoritative until the caller commits INSERT/DROP/RENAME.
        conn.execute(text('DROP TABLE IF EXISTS character_companion_messages_v2'))
        conn.execute(text('''CREATE TABLE character_companion_messages_v2 (
            id VARCHAR(36) PRIMARY KEY, turn_id VARCHAR(36) NOT NULL REFERENCES character_companion_turns(id) ON DELETE CASCADE,
            chat_id VARCHAR(36) NOT NULL REFERENCES character_companion_chats(id) ON DELETE CASCADE,
            role VARCHAR(20) NOT NULL, kind VARCHAR(20) NOT NULL DEFAULT 'final', sequence INTEGER NOT NULL DEFAULT 0,
            content TEXT NOT NULL, manifest JSON NOT NULL, committed BOOLEAN NOT NULL,
            created_at DATETIME NOT NULL, UNIQUE(turn_id, role, sequence))'''))
        kind = 'kind' if 'kind' in columns else "CASE WHEN role='user' THEN 'user' WHEN role='interim' THEN 'commentary' ELSE 'final' END"
        sequence = 'sequence' if 'sequence' in columns else '0'
        conn.execute(text(f'''INSERT INTO character_companion_messages_v2
            SELECT id, turn_id, chat_id, role, {kind}, {sequence}, content, manifest,
                   committed, created_at FROM character_companion_messages'''))
        conn.execute(text(f'DROP TABLE {table}'))
        conn.execute(text(f'ALTER TABLE character_companion_messages_v2 RENAME TO {table}'))
        for field in ('turn_id', 'chat_id'):
            conn.execute(text(f'CREATE INDEX ix_character_companion_messages_{field} ON {table} ({field})'))
    elif needs_upgrade:
        if 'kind' not in columns:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN kind VARCHAR(20) NOT NULL DEFAULT 'final'"))
        if 'sequence' not in columns:
            conn.execute(text(f'ALTER TABLE {table} ADD COLUMN sequence INTEGER NOT NULL DEFAULT 0'))
        for constraint in old:
            name = conn.dialect.identifier_preparer.quote(constraint['name'])
            conn.execute(text(f'ALTER TABLE {table} DROP CONSTRAINT {name}'))
        if not any(set(c['column_names']) == {'turn_id', 'role', 'sequence'} for c in constraints):
            conn.execute(text(f'ALTER TABLE {table} ADD CONSTRAINT uq_companion_message_sequence UNIQUE (turn_id, role, sequence)'))
    conn.execute(text(f"UPDATE {table} SET kind='user' WHERE role='user' AND kind<>'user'"))
    # Old transient interims may already coexist with newer commentary. Allocate
    # a free positive sequence per turn, without replacing any saved message.
    interims = conn.execute(text(f"SELECT id, turn_id FROM {table} WHERE role='interim' ORDER BY created_at, id")).fetchall()
    for identifier, turn_id in interims:
        sequence = conn.execute(text(f"SELECT COALESCE(MAX(sequence),0)+1 FROM {table} WHERE turn_id=:turn AND role='assistant'"), {'turn':turn_id}).scalar_one()
        conn.execute(text(f"UPDATE {table} SET role='assistant', kind='commentary', sequence=:sequence WHERE id=:id"), {'sequence':sequence,'id':identifier})
