"""One local installation record, independent of user and RPG datasets."""
from contextlib import contextmanager, closing
import os
from pathlib import Path
import sqlite3
from collections.abc import Iterator

from app.schemas.installation import InstallationState

class InstallationRepository:
    def __init__(self, directory: Path | None = None):
        self.path = (directory or Path(os.getenv("SHRECKNET_DATA_DIR", "/data"))) / "installation.db"

    @contextmanager
    def transaction(self) -> Iterator[tuple[sqlite3.Connection, InstallationState]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=5)) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS installation (id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL)")
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT state FROM installation WHERE id=1").fetchone()
            state = InstallationState.model_validate_json(row[0]) if row else InstallationState()
            yield connection, state
            connection.execute("INSERT INTO installation VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET state=excluded.state", (state.model_dump_json(),))

    def read(self) -> InstallationState:
        with self.transaction() as (_, state):
            return state
