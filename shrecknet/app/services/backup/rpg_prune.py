"""Reduce a ShreckRPG data directory to the data of a single Shrecknet user.

Every table of the database must have an explicit policy. An unknown table aborts the
operation, so a schema change can never silently leave another user's data behind.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RPG_DATABASE = "shreckrpg.db"
ASSETS_DIR = "chronicle-assets"

# Tables whose rows carry a user reference: table -> columns that must equal the kept user.
USER_COLUMNS: dict[str, tuple[str, ...]] = {
    "alerts": ("user_id",),
    "favorite_pages": ("user_id",),
    "game_memberships": ("user_id",),
    "game_session_attendance": ("user_id",),
    "game_session_poll_votes": ("user_id",),
    "note_shares": ("user_id",),
    "notes": ("owner_user_id",),
    "notification_preferences": ("user_id",),
    "notifications": ("user_id",),
    "page_user_visits": ("user_id",),
    "page_visits": ("user_id",),
    "responses": ("author_user_id",),
    "background_jobs": ("author_id",),
}
# Games are owned by exactly one user; the rest cascade through foreign keys.
OWNER_TABLES: dict[str, str] = {"games": "owner_user_id"}
# Tables with no per-user content that are kept as they are.
NEUTRAL_TABLES = frozenset(
    {
        "activities",
        "chronicle_assets",
        "chronicle_media",
        "chronicles",
        "game_session_poll_options",
        "game_session_polls",
        "game_sessions",
        "id_mappings",
        "migration_runs",
        "note_page_links",
        "page_visit_stats",
        "processed_events",
    }
)
# Rebuildable or outbound data that can embed other users' payloads.
CLEARED_TABLES = frozenset({"projection_cache", "shreckcompanion_webhook_outbox"})


class RpgPruneError(RuntimeError):
    pass


@dataclass
class PruneReport:
    kept_user_id: str
    deleted_rows: dict[str, int]
    deleted_asset_files: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "kept_user_id": self.kept_user_id,
            "deleted_rows": {table: count for table, count in self.deleted_rows.items() if count},
            "deleted_asset_files": self.deleted_asset_files,
        }


def _tables(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
    return [row[0] for row in rows]


def _count(connection: sqlite3.Connection, table: str) -> int:
    return connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


def prune_to_user(data_dir: Path, user_id: int | str) -> PruneReport:
    """Delete every record and chronicle asset that does not belong to ``user_id``."""
    kept = str(user_id)
    database = data_dir / RPG_DATABASE
    if not database.is_file():
        raise RpgPruneError(f"{RPG_DATABASE} is missing from the ShreckRPG data")

    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        tables = _tables(connection)
        known = set(USER_COLUMNS) | set(OWNER_TABLES) | NEUTRAL_TABLES | CLEARED_TABLES
        unknown = sorted(set(tables) - known)
        if unknown:
            raise RpgPruneError(
                "Cannot guarantee that only the selected user's data remains; "
                f"no pruning rule for table(s): {', '.join(unknown)}"
            )
        present = [table for table in tables if table in known]
        before = {table: _count(connection, table) for table in present}

        with connection:
            for table, column in OWNER_TABLES.items():
                if table in tables:
                    connection.execute(f'DELETE FROM "{table}" WHERE CAST("{column}" AS TEXT) <> ?', (kept,))
            for table, columns in USER_COLUMNS.items():
                if table in tables:
                    for column in columns:
                        connection.execute(f'DELETE FROM "{table}" WHERE CAST("{column}" AS TEXT) <> ?', (kept,))
            for table in CLEARED_TABLES & set(tables):
                connection.execute(f'DELETE FROM "{table}"')
            if "page_visit_stats" in tables and "page_user_visits" in tables:
                connection.execute(
                    "UPDATE page_visit_stats SET "
                    "total_visits = COALESCE((SELECT SUM(visit_count) FROM page_user_visits v WHERE v.page_key = page_visit_stats.page_key), 0), "
                    "unique_users = (SELECT COUNT(DISTINCT user_id) FROM page_user_visits v WHERE v.page_key = page_visit_stats.page_key)"
                )
                connection.execute("DELETE FROM page_visit_stats WHERE unique_users = 0")

        _assert_clean(connection, tables, kept)
        after = {table: _count(connection, table) for table in present}
        asset_ids = (
            {f"{row[0]}.{row[1]}" for row in connection.execute("SELECT id, extension FROM chronicle_assets")}
            if "chronicle_assets" in tables
            else set()
        )
        connection.execute("VACUUM")
    finally:
        connection.close()

    return PruneReport(kept, {table: before[table] - after[table] for table in present}, _prune_assets(data_dir, asset_ids))


def _assert_clean(connection: sqlite3.Connection, tables: list[str], kept: str) -> None:
    rules = [(table, column) for table, columns in USER_COLUMNS.items() for column in columns] + list(OWNER_TABLES.items())
    for table, column in rules:
        if table not in tables:
            continue
        leftover = connection.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE CAST("{column}" AS TEXT) <> ?', (kept,)
        ).fetchone()[0]
        if leftover:
            raise RpgPruneError(f"Pruning left {leftover} foreign row(s) in {table}.{column}")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise RpgPruneError("Pruning left dangling references")


def _prune_assets(data_dir: Path, keep: set[str]) -> int:
    root = data_dir / ASSETS_DIR
    if not root.is_dir():
        return 0
    removed = 0
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file() and path.name not in keep:
            path.unlink()
            removed += 1
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    return removed
