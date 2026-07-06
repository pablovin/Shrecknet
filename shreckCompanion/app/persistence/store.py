from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.schemas import PersonalCompanionAgentCreate, PersonalCompanionAgentRead, PersonalCompanionAgentUpdate


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)


def json_loads(raw: str | None) -> Any:
    if not raw:
        return None
    return json.loads(raw)


class CompanionStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
        settings.chats_dir.mkdir(parents=True, exist_ok=True)
        settings.local_tests_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = settings.database_path
        self.init_db()

    @staticmethod
    def default_chat_memory() -> dict[str, Any]:
        return {
            "summary": "",
            "active_entities": [],
            "open_topics": [],
            "last_resolved_subject": None,
        }

    @classmethod
    def normalize_chat_payload(cls, payload: dict[str, Any] | None, *, user_id: int, companion_id: str, session_id: str) -> dict[str, Any]:
        normalized = dict(payload or {})
        normalized.setdefault("user_id", user_id)
        normalized.setdefault("companion_id", companion_id)
        normalized.setdefault("session_id", session_id)
        normalized.setdefault("created_at", utc_now_iso())
        normalized.setdefault("messages", [])
        memory = normalized.get("memory")
        if not isinstance(memory, dict):
            memory = {}
        default_memory = cls.default_chat_memory()
        for key, value in default_memory.items():
            memory.setdefault(key, value)
        if not isinstance(memory.get("active_entities"), list):
            memory["active_entities"] = []
        if not isinstance(memory.get("open_topics"), list):
            memory["open_topics"] = []
        normalized["memory"] = memory
        return normalized

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS companions (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    avatar_url TEXT,
                    writing_style TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    companion_id TEXT NOT NULL,
                    ontology_id INTEGER NOT NULL,
                    title TEXT NOT NULL DEFAULT 'New chat',
                    allocated_tools TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_message_at TEXT
                );
                CREATE TABLE IF NOT EXISTS turn_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    session_id TEXT NOT NULL,
                    ontology_id INTEGER NOT NULL,
                    companion_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    query TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_turn_jobs_user_id ON turn_jobs(user_id);
                CREATE INDEX IF NOT EXISTS ix_turn_jobs_session_id ON turn_jobs(session_id);
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
            if "title" not in columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN title TEXT NOT NULL DEFAULT 'New chat'")
            if "last_message_at" not in columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN last_message_at TEXT")

    @staticmethod
    def _companion_from_row(row: sqlite3.Row) -> PersonalCompanionAgentRead:
        return PersonalCompanionAgentRead(
            id=str(row["id"]),
            user_id=int(row["user_id"]),
            name=str(row["name"]),
            avatar_url=row["avatar_url"],
            writing_style=str(row["writing_style"]),
            active=bool(row["active"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    def create_companion(self, user_id: int, payload: PersonalCompanionAgentCreate) -> PersonalCompanionAgentRead:
        now = utc_now_iso()
        companion_id = str(uuid4())
        with self.connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO companions (id,user_id,name,avatar_url,writing_style,active,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        companion_id,
                        user_id,
                        payload.name,
                        payload.avatar_url,
                        payload.writing_style,
                        1 if payload.active else 0,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Personal companion already exists for this user") from exc
        return self.get_companion(user_id)

    def get_companion(self, user_id: int) -> PersonalCompanionAgentRead:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM companions WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            raise KeyError("Personal companion not found")
        return self._companion_from_row(row)

    def update_companion(self, user_id: int, payload: PersonalCompanionAgentUpdate) -> PersonalCompanionAgentRead:
        current = self.get_companion(user_id)
        patch = payload.model_dump(exclude_unset=True)
        if not patch:
            return current
        values = {
            "name": patch.get("name", current.name),
            "avatar_url": patch.get("avatar_url", current.avatar_url),
            "writing_style": patch.get("writing_style", current.writing_style),
            "active": 1 if patch.get("active", current.active) else 0,
            "updated_at": utc_now_iso(),
            "user_id": user_id,
        }
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE companions
                SET name=:name, avatar_url=:avatar_url, writing_style=:writing_style,
                    active=:active, updated_at=:updated_at
                WHERE user_id=:user_id
                """,
                values,
            )
        return self.get_companion(user_id)

    def delete_companion(self, user_id: int) -> None:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM companions WHERE user_id = ?", (user_id,))
        if cursor.rowcount == 0:
            raise KeyError("Personal companion not found")

    def list_sessions(self, user_id: int, *, ontology_id: int | None = None, companion_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM sessions WHERE user_id = ?"
        params: list[Any] = [user_id]
        if ontology_id is not None:
            query += " AND ontology_id = ?"
            params.append(ontology_id)
        if companion_id is not None:
            query += " AND companion_id = ?"
            params.append(companion_id)
        query += " ORDER BY COALESCE(last_message_at, updated_at) DESC, created_at DESC"
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [
            {
                "session_id": str(row["session_id"]),
                "user_id": int(row["user_id"]),
                "companion_id": str(row["companion_id"]),
                "ontology_id": int(row["ontology_id"]),
                "title": str(row["title"] or "New chat"),
                "allocated_tools": json_loads(row["allocated_tools"]) or {},
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
                "last_message_at": str(row["last_message_at"]) if row["last_message_at"] else None,
            }
            for row in rows
        ]

    def create_session(
        self,
        *,
        user_id: int,
        companion_id: str,
        ontology_id: int,
        title: str,
        allocated_tools: dict[str, Any],
    ) -> dict[str, Any]:
        now = utc_now_iso()
        current_sessions = self.list_sessions(user_id, ontology_id=ontology_id)
        if len(current_sessions) >= int(self.settings.companion_chat_session_limit_per_ontology):
            raise ValueError("Companion chat session limit reached for this ontology")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (session_id,user_id,companion_id,ontology_id,title,allocated_tools,created_at,updated_at,last_message_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (str(uuid4()), user_id, companion_id, ontology_id, title or "New chat", json_dumps(allocated_tools), now, now, None),
            )
            row = conn.execute("SELECT * FROM sessions WHERE rowid = last_insert_rowid()").fetchone()
        session_id = str(row["session_id"])
        self.init_chat_file(user_id, companion_id, session_id)
        return self.get_session(user_id, session_id) or {}

    def get_session(self, user_id: int, session_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE user_id = ? AND session_id = ?",
                (user_id, session_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "session_id": str(row["session_id"]),
            "user_id": int(row["user_id"]),
            "companion_id": str(row["companion_id"]),
            "ontology_id": int(row["ontology_id"]),
            "title": str(row["title"] or "New chat"),
            "allocated_tools": json_loads(row["allocated_tools"]) or {},
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "last_message_at": str(row["last_message_at"]) if row["last_message_at"] else None,
        }

    def update_session_allocated_tools(self, user_id: int, session_id: str, allocated_tools: dict[str, Any]) -> dict[str, Any] | None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE sessions SET allocated_tools = ?, updated_at = ? WHERE user_id = ? AND session_id = ?",
                (json_dumps(allocated_tools), utc_now_iso(), user_id, session_id),
            )
        return self.get_session(user_id, session_id)

    def update_session_title(self, user_id: int, session_id: str, *, title: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE user_id = ? AND session_id = ?",
                (title, utc_now_iso(), user_id, session_id),
            )
        return self.get_session(user_id, session_id)

    def count_sessions_by_ontology(self, user_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT ontology_id, COUNT(*) AS session_count
                FROM sessions
                WHERE user_id = ?
                GROUP BY ontology_id
                ORDER BY ontology_id
                """,
                (user_id,),
            ).fetchall()
        return [
            {
                "ontology_id": int(row["ontology_id"]),
                "count": int(row["session_count"]),
                "limit": int(self.settings.companion_chat_session_limit_per_ontology),
            }
            for row in rows
        ]

    def delete_session(self, user_id: int, session_id: str) -> dict[str, Any] | None:
        session = self.get_session(user_id, session_id)
        if session is None:
            return None
        with self.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE user_id = ? AND session_id = ?", (user_id, session_id))
        path = self.chat_file_path(user_id, str(session["companion_id"]), session_id)
        path.unlink(missing_ok=True)
        return session

    def create_turn_job(
        self,
        *,
        user_id: int,
        session_id: str,
        ontology_id: int,
        companion_id: str,
        query: str,
        payload: dict[str, Any],
    ) -> int:
        now = utc_now_iso()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO turn_jobs (user_id,session_id,ontology_id,companion_id,status,query,payload,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (user_id, session_id, ontology_id, companion_id, "queued", query, json_dumps(payload), now, now),
            )
            job_id = int(cursor.lastrowid)
        self.write_turn_job_media_snapshot(job_id)
        return job_id

    def update_turn_job(self, job_id: int, *, status: str, payload: dict[str, Any], error: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE turn_jobs SET status = ?, payload = ?, error = ?, updated_at = ? WHERE id = ?",
                (status, json_dumps(payload), error, utc_now_iso(), job_id),
            )
        self.write_turn_job_media_snapshot(job_id)
        self.write_frontend_response_example(job_id)

    def get_turn_job(self, user_id: int, job_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM turn_jobs WHERE user_id = ? AND id = ?",
                (user_id, job_id),
            ).fetchone()
        if row is None:
            return None
        payload = json_loads(row["payload"]) or {}
        if row["error"] and "error" not in payload:
            payload["error"] = row["error"]
        return {
            "job_id": int(row["id"]),
            "status": str(row["status"]),
            "payload": payload,
        }

    def chat_file_path(self, user_id: int, companion_id: str, session_id: str) -> Path:
        root = self.settings.chats_dir / str(user_id) / str(companion_id)
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{session_id}.json"

    def init_chat_file(self, user_id: int, companion_id: str, session_id: str) -> None:
        path = self.chat_file_path(user_id, companion_id, session_id)
        if path.exists():
            return
        path.write_text(
            json_dumps(
                self.normalize_chat_payload(None, user_id=user_id, companion_id=companion_id, session_id=session_id)
            ),
            encoding="utf-8",
        )

    def reset_chat_file(self, user_id: int, companion_id: str, session_id: str) -> None:
        path = self.chat_file_path(user_id, companion_id, session_id)
        path.write_text(
            json_dumps(
                {
                    **self.normalize_chat_payload(None, user_id=user_id, companion_id=companion_id, session_id=session_id),
                    "updated_at": utc_now_iso(),
                }
            ),
            encoding="utf-8",
        )

    def append_chat_message(
        self,
        *,
        user_id: int,
        companion_id: str,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        path = self.chat_file_path(user_id, companion_id, session_id)
        if not path.exists():
            self.init_chat_file(user_id, companion_id, session_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        payload = self.normalize_chat_payload(payload, user_id=user_id, companion_id=companion_id, session_id=session_id)
        message: dict[str, Any] = {"role": role, "content": content, "ts": utc_now_iso()}
        if metadata:
            message["meta"] = metadata
        payload.setdefault("messages", []).append(message)
        payload["updated_at"] = utc_now_iso()
        path.write_text(json_dumps(payload), encoding="utf-8")
        with self.connect() as conn:
            conn.execute(
                "UPDATE sessions SET updated_at = ?, last_message_at = ? WHERE user_id = ? AND session_id = ?",
                (payload["updated_at"], message["ts"], user_id, session_id),
            )

    def update_chat_memory(
        self,
        *,
        user_id: int,
        companion_id: str,
        session_id: str,
        memory: dict[str, Any],
    ) -> dict[str, Any]:
        path = self.chat_file_path(user_id, companion_id, session_id)
        if not path.exists():
            self.init_chat_file(user_id, companion_id, session_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        normalized = self.normalize_chat_payload(payload, user_id=user_id, companion_id=companion_id, session_id=session_id)
        merged_memory = self.default_chat_memory()
        for key, value in dict(memory or {}).items():
            merged_memory[key] = value
        normalized["memory"] = merged_memory
        normalized["updated_at"] = utc_now_iso()
        path.write_text(json_dumps(normalized), encoding="utf-8")
        return normalized

    def read_chat_file(self, user_id: int, companion_id: str, session_id: str) -> dict[str, Any] | None:
        path = self.chat_file_path(user_id, companion_id, session_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return self.normalize_chat_payload(payload, user_id=user_id, companion_id=companion_id, session_id=session_id) if isinstance(payload, dict) else None

    def turn_job_media_snapshot_path(self, user_id: int, job_id: int) -> Path:
        root = self.settings.media_path / "turn_jobs" / str(user_id)
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{job_id}.json"

    def turn_job_media_snapshot_url(self, user_id: int, job_id: int) -> str:
        return f"{self.settings.media_base_url.rstrip('/')}/turn_jobs/{user_id}/{job_id}.json"

    def write_turn_job_media_snapshot(self, job_id: int) -> None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM turn_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return

        payload = json_loads(row["payload"]) or {}
        if row["error"] and "error" not in payload:
            payload["error"] = row["error"]

        snapshot = {
            "job_id": int(row["id"]),
            "status": str(row["status"]),
            "payload": payload,
        }

        path = self.turn_job_media_snapshot_path(int(row["user_id"]), int(row["id"]))
        temp_path = path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        temp_path.replace(path)

    def write_frontend_response_example(self, job_id: int) -> None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM turn_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return
        path = self.settings.local_tests_dir / "personal_companion" / "orchestrator" / "frontend_response_example.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "service": "ShreckCompanion",
                    "turn_result_response": {
                        "job_id": int(row["id"]),
                        "status": str(row["status"]),
                        "payload": json_loads(row["payload"]) or {},
                    },
                    "chat_file_path": str(self.chat_file_path(int(row["user_id"]), str(row["companion_id"]), str(row["session_id"]))),
                    "updated_at": utc_now_iso(),
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
