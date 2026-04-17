from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from neo4j import AsyncSession as Neo4jSession
from sqlalchemy.exc import OperationalError
from sqlalchemy import select

from app.core.config_store import CONFIG_DB_FILENAME, LEGACY_CONFIG_DB_FILENAME, get_settings
from app.db.init_db import init_db
from app.db.jobs_session import get_jobs_engine
from app.db.session import get_engine, get_sessionmaker
from app.models import (
    Agent,
    BackgroundJob,
    ElderChat,
    ElderChatHistory,
    IdMapping,
    LibraryItem,
    MigrationRun,
    Ontology,
    OntologyEntity,
    OntologyProperty,
    OntologyRelationship,
    User,
    World,
)
from app.models.agent import agent_ontologies
from app.services.ontology_instance_service import OntologyInstanceService


logger = logging.getLogger(__name__)


def _row_hash(row: dict[str, Any]) -> str:
    payload = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _fetch_rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    cursor = conn.execute(f"SELECT * FROM {table} ORDER BY rowid")
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _fetch_optional_rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    try:
        return _fetch_rows(conn, table)
    except sqlite3.DatabaseError:
        return []


def _extract_legacy_event_source(*texts: Any) -> str | None:
    for text in texts:
        if text is None:
            continue
        for line in str(text).splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("source:"):
                source = stripped.split(":", 1)[1].strip()
                return source or None
    return None


@dataclass
class PreservedUser:
    id: int
    username: str
    email: str
    full_name: str
    timezone: str
    role: Any
    hashed_password: str
    password: str
    avatar_url: str | None


class LegacyMonolithImportService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.media_root = Path(self.settings.media_root)
        self.backup_dir = self.media_root / "backups"
        self.uploaded_backup_dir = self.backup_dir / "upload"
        self.data_root = self._resolve_data_dir()
        self._ensure_dirs()

    async def import_backup_bytes(
        self,
        payload: bytes,
        *,
        graph_session: Neo4jSession,
        preserved_user: User,
    ) -> dict[str, Any]:
        archive_path = self.get_uploaded_backup_path(
            f"legacy_backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.zip"
        )
        archive_path.write_bytes(payload)
        return await self.import_backup_path(
            archive_path,
            graph_session=graph_session,
            preserved_user=preserved_user,
        )

    async def import_backup_path(
        self,
        archive_path: Path,
        *,
        graph_session: Neo4jSession,
        preserved_user: User,
    ) -> dict[str, Any]:
        self._stage(f"starting import from archive: {archive_path}")
        if not archive_path.exists():
            raise FileNotFoundError(f"Backup file not found: {archive_path}")

        snapshot = PreservedUser(
            id=preserved_user.id,
            username=preserved_user.username,
            email=preserved_user.email,
            full_name=preserved_user.full_name,
            timezone=preserved_user.timezone,
            role=preserved_user.role,
            hashed_password=preserved_user.hashed_password,
            password=preserved_user.password,
            avatar_url=preserved_user.avatar_url,
        )

        with tempfile.TemporaryDirectory(prefix="shrecknet_old_import_") as tmp_dir:
            extract_root = Path(tmp_dir)
            self._stage("extracting backup archive")
            self._extract_archive(archive_path.read_bytes(), extract_root)
            self._stage("locating legacy assets in extracted archive")
            source_db = self._find_backend_db(extract_root)
            media_source = self._find_media_dir(extract_root)
            graph_path = self._find_graph_path(extract_root)
            schema_path = self._find_schema_path(extract_root)

            self._stage("disposing current SQLite engines")
            get_engine().dispose()
            get_jobs_engine().dispose()

            self._stage("restoring media files")
            self._restore_media(media_source)

            self._stage("restoring Neo4j graph")
            neo4j_summary = await self._restore_neo4j_dump(graph_session, graph_path, schema_path)

            self._stage("restoring SQLite database from legacy backend_2.db")
            db_summary = self._restore_database_from_monolith(source_db, snapshot)
            self._stage("repairing restored Neo4j graph for shrecknet app expectations")
            graph_repair_summary = await self._repair_graph_after_import(graph_session)

        self._stage("legacy import completed successfully")
        return {
            "status": "success",
            "backup_kind": "legacy_monolith",
            "target": "shrecknet",
            "stored_backup_path": str(archive_path),
            "source_db": source_db.name,
            "neo4j": {**neo4j_summary, "repair": graph_repair_summary},
            "database": db_summary,
            "restart_required": False,
        }

    def get_uploaded_backup_path(self, filename: str) -> Path:
        safe_name = Path(filename).name
        return self.uploaded_backup_dir / safe_name

    def _ensure_dirs(self) -> None:
        self.media_root.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.uploaded_backup_dir.mkdir(parents=True, exist_ok=True)

    def _extract_archive(self, payload: bytes, destination: Path) -> None:
        if self._try_extract_zip(payload, destination):
            return
        if self._try_extract_tar_gz(payload, destination):
            return
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid backup archive. Expected a .zip or .tar.gz file",
        )

    def _try_extract_zip(self, payload: bytes, destination: Path) -> bool:
        try:
            with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
                for member in archive.infolist():
                    normalized = Path(member.filename)
                    if normalized.is_absolute() or ".." in normalized.parts:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Unsafe backup archive entry detected",
                        )
                archive.extractall(destination)
            return True
        except zipfile.BadZipFile:
            return False

    def _try_extract_tar_gz(self, payload: bytes, destination: Path) -> bool:
        try:
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
                for member in archive.getmembers():
                    normalized = Path(member.name)
                    if normalized.is_absolute() or ".." in normalized.parts:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Unsafe backup archive entry detected",
                        )
                archive.extractall(destination, filter="data")
            return True
        except (tarfile.TarError, OSError):
            return False

    def _find_backend_db(self, extract_root: Path) -> Path:
        matches = sorted(extract_root.rglob("backend_2.db"))
        if not matches:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Legacy backup must contain backend_2.db",
            )
        return matches[0]

    def _find_media_dir(self, extract_root: Path) -> Path:
        for candidate in sorted(extract_root.rglob("media")):
            if candidate.is_dir():
                return candidate
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Legacy backup must contain a media directory",
        )

    def _find_graph_path(self, extract_root: Path) -> Path:
        matches = sorted(path for path in extract_root.rglob("graph.json") if path.parent.name == "neo4j")
        if not matches:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Legacy backup must contain neo4j/graph.json",
            )
        return matches[0]

    def _find_schema_path(self, extract_root: Path) -> Path | None:
        matches = sorted(path for path in extract_root.rglob("schema.json") if path.parent.name == "neo4j")
        return matches[0] if matches else None

    def _restore_media(self, source_dir: Path) -> None:
        self.media_root.mkdir(parents=True, exist_ok=True)

        for item in list(self.media_root.iterdir()):
            if item.name == "backups":
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

        for item in sorted(source_dir.iterdir()):
            if item.name == "backups":
                continue
            dest = self.media_root / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

    def _normalize_legacy_media_path(self, raw_path: str | None) -> str:
        path = str(raw_path or "").strip().replace("\\", "/")
        if not path:
            return ""
        if path.startswith("file://"):
            path = path[7:]
        for prefix in ("/app/media/", "./media/", "media/"):
            if path.startswith(prefix):
                path = path[len(prefix) :]
                break
        if path.startswith("/"):
            path = path[1:]
        return path
    def _normalize_legacy_media_url(self, raw_value: str | None) -> str | None:
        value = str(raw_value or "").strip()
        if not value:
            return None

        lowered = value.lower()
        if lowered.startswith(("http://", "https://", "data:")):
            return value

        media_public = (self.settings.media_public_url or "").rstrip("/")
        media_base_url = self.settings.media_base_url.rstrip("/")
        target_base = media_public or media_base_url

        if target_base and value.startswith(f"{target_base}/"):
            return value

        normalized = self._normalize_legacy_media_path(value)
        if not normalized:
            return None
        if normalized.startswith("media/"):
            normalized = normalized[len("media/") :]

        if target_base:
            return f"{target_base}/{normalized}"
        return f"/media/{normalized}"

    def _normalize_legacy_media_urls(self, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            url = self._normalize_legacy_media_url(value)
            if url:
                normalized.append(url)
        return normalized

    def _resolve_library_pdf_path(
        self,
        raw_path: str | None,
        *,
        ontology_id: int,
        item_id: int,
    ) -> str:
        normalized = self._normalize_legacy_media_path(raw_path)
        candidates: list[str] = []
        if normalized:
            candidates.append(normalized)
            if normalized.startswith("media/"):
                candidates.append(normalized[len("media/") :])
            if not normalized.startswith("library/"):
                candidates.append(f"library/{ontology_id}/{item_id}/{Path(normalized).name}")

        candidates.extend(
            [
                f"library/{ontology_id}/{item_id}/content.pdf",
                f"library/{ontology_id}/{item_id}/{Path(normalized).name if normalized else 'content.pdf'}",
            ]
        )

        seen: set[str] = set()
        for rel in candidates:
            if not rel or rel in seen:
                continue
            seen.add(rel)
            if (self.media_root / rel).exists():
                return rel

        if normalized:
            filename = Path(normalized).name
            if filename:
                for match in sorted((self.media_root / "library").rglob(filename)) if (self.media_root / "library").exists() else []:
                    try:
                        return str(match.relative_to(self.media_root)).replace("\\", "/")
                    except ValueError:
                        continue

        return normalized

    def _restore_database_from_monolith(self, source_db: Path, preserved_user: PreservedUser) -> dict[str, Any]:
        self._stage("validating SQLite data directory access")
        self._validate_sqlite_storage_access()
        self._stage("resetting target SQLite databases")
        self._reset_target_databases()
        self._stage("initializing fresh SQLite schema")
        self._reinitialize_database_with_retry()
        self._stage("restoring preserved admin user")
        self._restore_preserved_user(preserved_user)
        self._stage("importing relational rows from legacy backend_2.db")
        stats = self._import_monolith_rows(source_db, preserved_user)
        return {"stats": stats, "preserved_user_id": preserved_user.id}

    def _reset_target_databases(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        keep = {
            CONFIG_DB_FILENAME,
            LEGACY_CONFIG_DB_FILENAME,
            self._sqlite_url_to_path(self.settings.jobs_database_url).name,
        }
        for db_file in sorted(self.data_root.glob("*.db")):
            if db_file.name in keep:
                continue
            try:
                db_file.unlink()
            except OSError as exc:
                raise RuntimeError(
                    f"Unable to delete SQLite file '{db_file}'. "
                    "The data directory may be mounted read-only or owned by another user."
                ) from exc
            self._remove_sqlite_sidecars(db_file)

        import app.db.session as session_module

        session_module._engine = None
        session_module._sessionmaker = None
        session_module._engine_key = None

    def _reinitialize_database_with_retry(self) -> None:
        try:
            init_db()
            return
        except OperationalError as exc:
            if "disk I/O error" not in str(exc):
                raise

        db_path = self._sqlite_url_to_path(self.settings.database_url)
        self._remove_sqlite_sidecars(db_path)
        if db_path.exists():
            db_path.unlink()

        import app.db.session as session_module

        session_module._engine = None
        session_module._sessionmaker = None
        session_module._engine_key = None

        init_db()

    def _remove_sqlite_sidecars(self, db_path: Path) -> None:
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(f"{db_path.as_posix()}{suffix}")
            if sidecar.exists():
                sidecar.unlink()

    def _validate_sqlite_storage_access(self) -> None:
        db_path = self._sqlite_url_to_path(self.settings.database_url)
        jobs_path = self._sqlite_url_to_path(self.settings.jobs_database_url)

        required_dirs: list[Path] = []
        for parent in (db_path.parent, jobs_path.parent):
            if parent not in required_dirs:
                required_dirs.append(parent)

        for directory in required_dirs:
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise RuntimeError(
                    f"Failed to create SQLite data directory '{directory}': {exc}."
                ) from exc

            probe = directory / f".legacy-import-write-check-{uuid4().hex}"
            try:
                probe.write_text("ok", encoding="utf-8")
            except OSError as exc:
                raise RuntimeError(
                    f"SQLite data directory '{directory}' is not writable for uid={os.getuid()} gid={os.getgid()}. "
                    "Fix host mount ownership/permissions (for example: chown -R 1000:1000 shrecknet/databases shrecknet/media)."
                ) from exc
            finally:
                if probe.exists():
                    probe.unlink()

    def _restore_preserved_user(self, preserved_user: PreservedUser) -> None:
        sm = get_sessionmaker()
        with sm() as session:
            existing = session.get(User, preserved_user.id)
            if existing is None:
                existing = User(
                    id=preserved_user.id,
                    username=preserved_user.username,
                    hashed_password=preserved_user.hashed_password,
                    password=preserved_user.password,
                    full_name=preserved_user.full_name,
                    email=preserved_user.email,
                    timezone=preserved_user.timezone,
                    role=preserved_user.role,
                    avatar_url=preserved_user.avatar_url,
                )
                session.add(existing)
            else:
                existing.username = preserved_user.username
                existing.hashed_password = preserved_user.hashed_password
                existing.password = preserved_user.password
                existing.full_name = preserved_user.full_name
                existing.email = preserved_user.email
                existing.timezone = preserved_user.timezone
                existing.role = preserved_user.role
                existing.avatar_url = preserved_user.avatar_url
            session.commit()

    def _import_monolith_rows(self, source_path: Path, preserved_user: PreservedUser) -> dict[str, dict[str, int]]:
        run_id = f"legacy-{uuid4().hex[:12]}"

        src = sqlite3.connect(source_path)
        try:
            users = _fetch_rows(src, "users")
            ontologies = _fetch_rows(src, "ontologies")
            ontology_entities = _fetch_optional_rows(src, "ontology_entities")
            entity_properties = _fetch_optional_rows(src, "entity_properties")
            entity_relationships = _fetch_optional_rows(src, "entity_relationships")
            agents = _fetch_rows(src, "agents")
            agent_ontology_rows = _fetch_optional_rows(src, "agent_ontologies")
            jobs = _fetch_rows(src, "background_jobs")
            library_items = _fetch_rows(src, "library_items")
            elder_chats = _fetch_optional_rows(src, "elder_chats")
            elder_chat_history = _fetch_optional_rows(src, "elder_chat_history")
        except sqlite3.DatabaseError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid legacy backend_2.db: {exc}",
            ) from exc
        finally:
            src.close()

        stats = {
            "users": {"imported": 0, "skipped": 0},
            "ontologies": {"imported": 0, "skipped": 0},
            "ontology_entities": {"imported": 0, "skipped": 0},
            "entity_properties": {"imported": 0, "skipped": 0},
            "entity_relationships": {"imported": 0, "skipped": 0},
            "agents": {"imported": 0, "skipped": 0},
            "agent_ontologies": {"imported": 0, "skipped": 0},
            "background_jobs": {"imported": 0, "skipped": 0},
            "library_items": {"imported": 0, "skipped": 0},
            "elder_chats": {"imported": 0, "skipped": 0},
            "elder_chat_history": {"imported": 0, "skipped": 0},
        }

        sm = get_sessionmaker()
        with sm() as session:
            default_ontology_id_cache: int | None = None

            run = MigrationRun(
                run_id=run_id,
                source_db_path=str(source_path),
                target_db_url=os.getenv("SHRECKNET_DATABASE_URL", ""),
                status="running",
            )
            session.merge(run)
            session.commit()

            def get_map(source_table: str, source_id: str) -> IdMapping | None:
                return session.get(IdMapping, (source_table, source_id))

            def parse_list(raw: Any) -> list[str]:
                if raw is None:
                    return []
                if isinstance(raw, list):
                    return [str(item) for item in raw if str(item).strip()]
                raw_str = str(raw).strip()
                if not raw_str:
                    return []
                try:
                    loaded = json.loads(raw_str)
                    if isinstance(loaded, list):
                        return [str(item) for item in loaded if str(item).strip()]
                except Exception:
                    pass
                return [part.strip() for part in raw_str.split(",") if part.strip()]

            def normalize_author_type(raw: Any) -> str:
                value = str(raw or "human").strip().lower()
                return "agent" if value == "agent" else "human"

            def normalize_cardinality(raw: Any) -> str:
                value = str(raw or "one").strip().lower()
                return "many" if value == "many" else "one"

            def normalize_property_data_type(raw: Any) -> str:
                value = str(raw or "text").strip().lower()
                allowed = {
                    "text",
                    "number",
                    "image",
                    "date",
                    "foundry_character_sheet_json",
                    "pdf_link",
                    "website_link",
                    "youtube_link",
                    "suno_link",
                    "spotify_link",
                }
                return value if value in allowed else "text"

            def ensure_default_ontology_id() -> int:
                nonlocal default_ontology_id_cache

                if default_ontology_id_cache is not None:
                    return default_ontology_id_cache

                # Make pending inserts from earlier import phases visible to SELECT.
                session.flush()

                existing = session.execute(select(Ontology.id).order_by(Ontology.id.asc()).limit(1)).scalar_one_or_none()
                if existing is not None:
                    default_ontology_id_cache = int(existing)
                    return default_ontology_id_cache

                # Only reached when the legacy dump has no ontologies at all.
                fallback_world_id = "world-import-fallback"
                world = session.get(World, fallback_world_id)
                if world is None:
                    world = World(id=fallback_world_id, name="Imported World 1")
                    session.add(world)

                fallback_ontology_id = 1
                ontology = session.get(Ontology, fallback_ontology_id)
                if ontology is None:
                    ontology = Ontology(
                        id=fallback_ontology_id,
                        world_id=fallback_world_id,
                        name="Imported Ontology 1",
                    )
                    session.add(ontology)

                session.flush()
                default_ontology_id_cache = fallback_ontology_id
                return default_ontology_id_cache

            for row in users:
                source_id = str(row["id"])
                if self._should_preserve_user_row(row, preserved_user):
                    stats["users"]["skipped"] += 1
                    continue

                row_hash = _row_hash(row)
                mapping = get_map("users", source_id)
                if mapping and mapping.source_hash == row_hash:
                    stats["users"]["skipped"] += 1
                    continue

                target = session.get(User, int(source_id))
                if target is None:
                    target = User(id=int(source_id), email="", full_name="", role="player", password="")
                    session.add(target)
                target.username = str(row.get("username") or row.get("email") or source_id)
                target.email = str(row.get("email") or "")
                target.full_name = str(row.get("full_name") or "")
                target.role = str(row.get("role") or "player")
                target.hashed_password = str(row.get("hashed_password") or "")
                target.password = str(row.get("hashed_password") or "")
                target.timezone = str(row.get("timezone") or "UTC")
                target.avatar_url = self._normalize_legacy_media_url(row.get("avatar_url"))

                session.merge(
                    IdMapping(
                        source_table="users",
                        source_id=source_id,
                        target_table="users",
                        target_id=source_id,
                        source_hash=row_hash,
                    )
                )
                stats["users"]["imported"] += 1

            for row in ontologies:
                source_id = str(row["id"])
                row_hash = _row_hash(row)
                world_id = f"world-onto-{source_id}"
                try:
                    ontology_id = int(source_id)
                except (TypeError, ValueError):
                    logger.warning("Skipping ontology row with non-integer id: %s", source_id)
                    stats["ontologies"]["skipped"] += 1
                    continue

                world_map = get_map("ontologies:world", source_id)
                ontology_map = get_map("ontologies", source_id)
                unchanged = (
                    world_map is not None
                    and ontology_map is not None
                    and world_map.source_hash == row_hash
                    and ontology_map.source_hash == row_hash
                )
                if unchanged:
                    stats["ontologies"]["skipped"] += 1
                    continue

                world = session.get(World, world_id)
                if world is None:
                    world = World(id=world_id, name="")
                    session.add(world)
                world.name = str(row.get("name") or f"World {source_id}")

                ontology = session.get(Ontology, ontology_id)
                if ontology is None:
                    ontology = Ontology(id=ontology_id, world_id=world_id, name="")
                    session.add(ontology)
                ontology.world_id = world_id
                ontology.name = str(row.get("name") or f"Ontology {source_id}")
                ontology.description = (
                    str(row.get("description"))
                    if row.get("description") is not None
                    else None
                )
                ontology.image_url = self._normalize_legacy_media_url(row.get("image_url"))

                session.merge(
                    IdMapping(
                        source_table="ontologies:world",
                        source_id=source_id,
                        target_table="worlds",
                        target_id=world_id,
                        source_hash=row_hash,
                    )
                )
                session.merge(
                    IdMapping(
                        source_table="ontologies",
                        source_id=source_id,
                        target_table="ontologies",
                        target_id=str(ontology_id),
                        source_hash=row_hash,
                    )
                )
                stats["ontologies"]["imported"] += 1

            session.flush()

            for row in ontology_entities:
                source_id = str(row.get("id") or "")
                if not source_id:
                    stats["ontology_entities"]["skipped"] += 1
                    continue
                row_hash = _row_hash(row)
                mapping = get_map("ontology_entities", source_id)
                if mapping and mapping.source_hash == row_hash:
                    stats["ontology_entities"]["skipped"] += 1
                    continue

                mapped_ontology_id = str(row.get("ontology_id") or "")
                ontology_target = get_map("ontologies", mapped_ontology_id)
                if ontology_target is None:
                    stats["ontology_entities"]["skipped"] += 1
                    continue

                try:
                    target_id = int(source_id)
                    ontology_id = int(ontology_target.target_id)
                except (TypeError, ValueError):
                    stats["ontology_entities"]["skipped"] += 1
                    continue

                entity = session.get(OntologyEntity, target_id)
                if entity is None:
                    entity = OntologyEntity(
                        id=target_id,
                        ontology_id=ontology_id,
                        name=str(row.get("name") or f"Entity {source_id}"),
                        author_type="human",
                    )
                    session.add(entity)

                entity.ontology_id = ontology_id
                entity.name = str(row.get("name") or f"Entity {source_id}")
                entity.description = str(row.get("description")) if row.get("description") is not None else None
                entity.image_url = self._normalize_legacy_media_url(row.get("image_url"))
                entity.keywords = parse_list(row.get("keywords"))
                entity.display_on_world = bool(row.get("display_on_world", True))
                entity.auto_generatable = bool(row.get("auto_generatable", False))
                entity.author_type = normalize_author_type(row.get("author_type"))
                entity.user_id = str(row.get("user_id")) if row.get("user_id") is not None else None
                entity.agent_id = str(row.get("agent_id")) if row.get("agent_id") is not None else None

                session.merge(
                    IdMapping(
                        source_table="ontology_entities",
                        source_id=source_id,
                        target_table="ontology_entities",
                        target_id=str(target_id),
                        source_hash=row_hash,
                    )
                )
                stats["ontology_entities"]["imported"] += 1

            session.flush()

            for row in entity_properties:
                source_id = str(row.get("id") or "")
                if not source_id:
                    stats["entity_properties"]["skipped"] += 1
                    continue
                row_hash = _row_hash(row)
                mapping = get_map("entity_properties", source_id)
                if mapping and mapping.source_hash == row_hash:
                    stats["entity_properties"]["skipped"] += 1
                    continue

                mapped_entity_id = str(row.get("entity_id") or "")
                entity_target = get_map("ontology_entities", mapped_entity_id)
                if entity_target is None:
                    stats["entity_properties"]["skipped"] += 1
                    continue

                try:
                    target_id = int(source_id)
                    entity_id = int(entity_target.target_id)
                except (TypeError, ValueError):
                    stats["entity_properties"]["skipped"] += 1
                    continue

                prop = session.get(OntologyProperty, target_id)
                if prop is None:
                    prop = OntologyProperty(
                        id=target_id,
                        entity_id=entity_id,
                        name=str(row.get("name") or f"Property {source_id}"),
                        cardinality="one",
                        data_type="text",
                        author_type="human",
                    )
                    session.add(prop)

                prop.entity_id = entity_id
                prop.name = str(row.get("name") or f"Property {source_id}")
                prop.description = str(row.get("description")) if row.get("description") is not None else None
                prop.image_url = self._normalize_legacy_media_url(row.get("image_url"))
                prop.cardinality = normalize_cardinality(row.get("cardinality"))
                prop.data_type = normalize_property_data_type(row.get("data_type"))
                prop.auto_generatable = bool(row.get("auto_generatable", False))
                prop.author_type = normalize_author_type(row.get("author_type"))
                prop.user_id = str(row.get("user_id")) if row.get("user_id") is not None else None
                prop.agent_id = str(row.get("agent_id")) if row.get("agent_id") is not None else None

                session.merge(
                    IdMapping(
                        source_table="entity_properties",
                        source_id=source_id,
                        target_table="entity_properties",
                        target_id=str(target_id),
                        source_hash=row_hash,
                    )
                )
                stats["entity_properties"]["imported"] += 1

            for row in entity_relationships:
                source_id = str(row.get("id") or "")
                if not source_id:
                    stats["entity_relationships"]["skipped"] += 1
                    continue
                row_hash = _row_hash(row)
                mapping = get_map("entity_relationships", source_id)
                if mapping and mapping.source_hash == row_hash:
                    stats["entity_relationships"]["skipped"] += 1
                    continue

                mapped_entity_id = str(row.get("entity_id") or "")
                entity_target = get_map("ontology_entities", mapped_entity_id)
                if entity_target is None:
                    stats["entity_relationships"]["skipped"] += 1
                    continue

                mapped_destiny_id = str(row.get("destiny_entity_id") or "")
                destiny_target = get_map("ontology_entities", mapped_destiny_id) if mapped_destiny_id else None

                try:
                    target_id = int(source_id)
                    entity_id = int(entity_target.target_id)
                    destiny_entity_id = int(destiny_target.target_id) if destiny_target is not None else None
                except (TypeError, ValueError):
                    stats["entity_relationships"]["skipped"] += 1
                    continue

                relationship = session.get(OntologyRelationship, target_id)
                if relationship is None:
                    relationship = OntologyRelationship(
                        id=target_id,
                        entity_id=entity_id,
                        destiny_entity_id=destiny_entity_id,
                        name=str(row.get("name") or f"Relationship {source_id}"),
                        author_type="human",
                    )
                    session.add(relationship)

                relationship.entity_id = entity_id
                relationship.destiny_entity_id = destiny_entity_id
                relationship.name = str(row.get("name") or f"Relationship {source_id}")
                relationship.description = str(row.get("description")) if row.get("description") is not None else None
                relationship.image_urls = self._normalize_legacy_media_urls(parse_list(row.get("image_urls")))
                relationship.bi_directional = bool(row.get("bi_directional", False))
                relationship.auto_generatable = bool(row.get("auto_generatable", False))
                relationship.author_type = normalize_author_type(row.get("author_type"))
                relationship.user_id = str(row.get("user_id")) if row.get("user_id") is not None else None
                relationship.agent_id = str(row.get("agent_id")) if row.get("agent_id") is not None else None

                session.merge(
                    IdMapping(
                        source_table="entity_relationships",
                        source_id=source_id,
                        target_table="entity_relationships",
                        target_id=str(target_id),
                        source_hash=row_hash,
                    )
                )
                stats["entity_relationships"]["imported"] += 1

            for row in agents:
                source_id = str(row["id"])
                row_hash = _row_hash(row)
                mapping = get_map("agents", source_id)
                if mapping and mapping.source_hash == row_hash:
                    stats["agents"]["skipped"] += 1
                    continue

                agent = session.get(Agent, source_id)
                if agent is None:
                    agent = Agent(id=source_id, name="", job="elder")
                    session.add(agent)
                agent.name = str(row.get("name") or source_id)
                raw_job = str(row.get("job") or row.get("kind") or "elder").strip().lower()
                if raw_job not in {"elder", "librarian", "architect", "novelist"}:
                    raw_job = "elder"
                agent.job = raw_job
                agent.active = bool(row.get("active", True))
                agent.avatar_url = self._normalize_legacy_media_url(row.get("avatar_url"))
                agent.description = str(row.get("description")) if row.get("description") is not None else None
                agent.writing_style = str(row.get("writing_style")) if row.get("writing_style") is not None else None

                session.merge(
                    IdMapping(
                        source_table="agents",
                        source_id=source_id,
                        target_table="agents",
                        target_id=source_id,
                        source_hash=row_hash,
                    )
                )
                stats["agents"]["imported"] += 1

            for row in agent_ontology_rows:
                source_agent_id = str(row.get("agent_id") or "").strip()
                source_ontology_id = str(row.get("ontology_id") or "").strip()
                if not source_agent_id or not source_ontology_id:
                    stats["agent_ontologies"]["skipped"] += 1
                    continue

                ontology_map = get_map("ontologies", source_ontology_id)
                if ontology_map is None:
                    stats["agent_ontologies"]["skipped"] += 1
                    continue

                try:
                    target_ontology_id = int(ontology_map.target_id)
                except (TypeError, ValueError):
                    stats["agent_ontologies"]["skipped"] += 1
                    continue

                link_exists = session.execute(
                    select(agent_ontologies.c.agent_id).where(
                        agent_ontologies.c.agent_id == source_agent_id,
                        agent_ontologies.c.ontology_id == target_ontology_id,
                    )
                ).scalar_one_or_none()
                if link_exists is not None:
                    stats["agent_ontologies"]["skipped"] += 1
                    continue

                session.execute(
                    agent_ontologies.insert().values(
                        agent_id=source_agent_id,
                        ontology_id=target_ontology_id,
                    )
                )
                stats["agent_ontologies"]["imported"] += 1

            for row in jobs:
                source_id = str(row["id"])
                target_id = int(source_id)
                row_hash = _row_hash(row)
                mapping = get_map("background_jobs", source_id)
                if mapping and mapping.source_hash == row_hash:
                    stats["background_jobs"]["skipped"] += 1
                    continue

                job = session.get(BackgroundJob, target_id)
                if job is None:
                    job = BackgroundJob(
                        id=target_id,
                        author_type="user",
                        author_id="legacy-import",
                        kind="legacy_import",
                        job_type="legacy_import",
                        status="queued",
                        description="",
                    )
                    session.add(job)
                raw_kind = str(row.get("job_type") or "legacy_import")
                raw_status = str(row.get("status") or "queued")
                allowed_statuses = {"queued", "running", "done", "failed"}
                normalized_status = raw_status if raw_status in allowed_statuses else "failed"

                job.kind = raw_kind
                job.job_type = raw_kind
                job.status = normalized_status
                job.description = str(row.get("description") or f"Imported legacy job {source_id}")

                session.merge(
                    IdMapping(
                        source_table="background_jobs",
                        source_id=source_id,
                        target_table="background_jobs",
                        target_id=str(target_id),
                        source_hash=row_hash,
                    )
                )
                stats["background_jobs"]["imported"] += 1

            for row in library_items:
                source_id = str(row["id"])
                target_id = int(source_id)
                row_hash = _row_hash(row)
                mapping = get_map("library_items", source_id)
                if mapping and mapping.source_hash == row_hash:
                    stats["library_items"]["skipped"] += 1
                    continue

                mapped_ontology_id = str(row.get("ontology_id") or "")
                ontology_target = session.get(IdMapping, ("ontologies", mapped_ontology_id))
                ontology_id: int
                if ontology_target is None:
                    ontology_id = ensure_default_ontology_id()
                else:
                    try:
                        ontology_id = int(ontology_target.target_id)
                    except (TypeError, ValueError):
                        logger.warning(
                            "Invalid ontology mapping target_id=%s for source ontology_id=%s; using default",
                            ontology_target.target_id,
                            mapped_ontology_id,
                        )
                        ontology_id = ensure_default_ontology_id()

                item = session.get(LibraryItem, target_id)
                if item is None:
                    item = LibraryItem(id=target_id, ontology_id=ontology_id, title="", pdf_path="")
                    session.add(item)
                item.ontology_id = ontology_id
                item.title = str(row.get("title") or f"Library Item {source_id}")
                item.authors = str(row.get("authors")) if row.get("authors") is not None else None
                item.description = str(row.get("description")) if row.get("description") is not None else None
                item.cover_url = self._normalize_legacy_media_url(row.get("cover_url"))
                item.pdf_path = self._resolve_library_pdf_path(
                    row.get("pdf_path"),
                    ontology_id=ontology_id,
                    item_id=target_id,
                )
                item.vectorized = bool(row.get("vectorized") or False)

                session.merge(
                    IdMapping(
                        source_table="library_items",
                        source_id=source_id,
                        target_table="library_items",
                        target_id=str(target_id),
                        source_hash=row_hash,
                    )
                )
                stats["library_items"]["imported"] += 1

            for row in elder_chats:
                source_id = str(row.get("id") or "").strip()
                if not source_id:
                    stats["elder_chats"]["skipped"] += 1
                    continue
                row_hash = _row_hash(row)
                mapping = get_map("elder_chats", source_id)
                if mapping and mapping.source_hash == row_hash:
                    stats["elder_chats"]["skipped"] += 1
                    continue

                source_user_id = str(row.get("user_id") or "").strip()
                user_target = get_map("users", source_user_id) if source_user_id else None
                user_id: int | None = None
                if user_target is not None:
                    try:
                        user_id = int(user_target.target_id)
                    except (TypeError, ValueError):
                        user_id = None
                elif source_user_id:
                    try:
                        fallback_user_id = int(source_user_id)
                    except (TypeError, ValueError):
                        fallback_user_id = None
                    if fallback_user_id is not None and session.get(User, fallback_user_id) is not None:
                        user_id = fallback_user_id

                source_agent_id = str(row.get("agent_id") or "").strip()
                agent_target = get_map("agents", source_agent_id) if source_agent_id else None
                agent_id: str | None = None
                if agent_target is not None:
                    agent_id = str(agent_target.target_id)
                elif source_agent_id and session.get(Agent, source_agent_id) is not None:
                    agent_id = source_agent_id

                if user_id is None or not agent_id:
                    stats["elder_chats"]["skipped"] += 1
                    continue

                chat = session.get(ElderChat, source_id)
                if chat is None:
                    chat = ElderChat(id=source_id, user_id=user_id, agent_id=agent_id, name="")
                    session.add(chat)

                chat.user_id = user_id
                chat.agent_id = agent_id
                chat.name = str(row.get("name") or row.get("title") or f"Chat {source_id}")
                chat.color = str(row.get("color")) if row.get("color") is not None else None

                session.merge(
                    IdMapping(
                        source_table="elder_chats",
                        source_id=source_id,
                        target_table="elder_chats",
                        target_id=source_id,
                        source_hash=row_hash,
                    )
                )
                stats["elder_chats"]["imported"] += 1

            session.flush()

            for row in elder_chat_history:
                source_id = str(row.get("id") or "").strip()
                if not source_id:
                    stats["elder_chat_history"]["skipped"] += 1
                    continue
                row_hash = _row_hash(row)
                mapping = get_map("elder_chat_history", source_id)
                if mapping and mapping.source_hash == row_hash:
                    stats["elder_chat_history"]["skipped"] += 1
                    continue

                source_chat_id = str(row.get("chat_id") or "").strip()
                chat_target = get_map("elder_chats", source_chat_id) if source_chat_id else None
                chat_id = str(chat_target.target_id) if chat_target is not None else source_chat_id
                if not chat_id or session.get(ElderChat, chat_id) is None:
                    stats["elder_chat_history"]["skipped"] += 1
                    continue

                try:
                    target_id = int(source_id)
                except (TypeError, ValueError):
                    stats["elder_chat_history"]["skipped"] += 1
                    continue

                history = session.get(ElderChatHistory, target_id)
                if history is None:
                    history = ElderChatHistory(
                        id=target_id,
                        chat_id=chat_id,
                        role="user",
                        content="",
                    )
                    session.add(history)

                history.chat_id = chat_id
                history.role = str(row.get("role") or "user")
                history.content = str(row.get("content") or row.get("message") or "")

                session.merge(
                    IdMapping(
                        source_table="elder_chat_history",
                        source_id=source_id,
                        target_table="elder_chat_history",
                        target_id=str(target_id),
                        source_hash=row_hash,
                    )
                )
                stats["elder_chat_history"]["imported"] += 1

            run.status = "completed"
            run.completed_at = datetime.now(timezone.utc)
            run.summary_json = json.dumps(stats, sort_keys=True)
            session.merge(run)
            session.flush()
            self._verify_imported_ontology_definitions(
                session,
                ontologies=ontologies,
                ontology_entities=ontology_entities,
                entity_properties=entity_properties,
                entity_relationships=entity_relationships,
            )
            session.commit()

        return stats

    def _verify_imported_ontology_definitions(
        self,
        session,
        *,
        ontologies: list[dict[str, Any]],
        ontology_entities: list[dict[str, Any]],
        entity_properties: list[dict[str, Any]],
        entity_relationships: list[dict[str, Any]],
    ) -> None:
        missing: list[str] = []

        for row in ontologies:
            source_id = str(row.get("id") or "")
            if not source_id:
                missing.append("ontology:<missing-id>")
                continue
            mapping = session.get(IdMapping, ("ontologies", source_id))
            if mapping is None:
                missing.append(f"ontology:{source_id}:mapping")
                continue
            try:
                target_id = int(mapping.target_id)
            except (TypeError, ValueError):
                missing.append(f"ontology:{source_id}:bad-target-id")
                continue
            if session.get(Ontology, target_id) is None:
                missing.append(f"ontology:{source_id}:record")

        for row in ontology_entities:
            source_id = str(row.get("id") or "")
            if not source_id:
                missing.append("ontology_entity:<missing-id>")
                continue
            try:
                target_id = int(source_id)
            except (TypeError, ValueError):
                missing.append(f"ontology_entity:{source_id}:bad-target-id")
                continue
            if session.get(OntologyEntity, target_id) is None:
                missing.append(f"ontology_entity:{source_id}:record")

        for row in entity_properties:
            source_id = str(row.get("id") or "")
            if not source_id:
                missing.append("entity_property:<missing-id>")
                continue
            try:
                target_id = int(source_id)
            except (TypeError, ValueError):
                missing.append(f"entity_property:{source_id}:bad-target-id")
                continue
            if session.get(OntologyProperty, target_id) is None:
                missing.append(f"entity_property:{source_id}:record")

        for row in entity_relationships:
            source_id = str(row.get("id") or "")
            if not source_id:
                missing.append("entity_relationship:<missing-id>")
                continue
            try:
                target_id = int(source_id)
            except (TypeError, ValueError):
                missing.append(f"entity_relationship:{source_id}:bad-target-id")
                continue
            if session.get(OntologyRelationship, target_id) is None:
                missing.append(f"entity_relationship:{source_id}:record")

        if missing:
            details = ", ".join(missing[:10])
            if len(missing) > 10:
                details += f" (+{len(missing) - 10} more)"
            raise ValueError(
                "Legacy import verification failed for ontology definitions: "
                f"{details}"
            )

    def _should_preserve_user_row(self, row: dict[str, Any], preserved_user: PreservedUser) -> bool:
        row_id = str(row.get("id") or "")
        username = str(row.get("username") or "")
        email = str(row.get("email") or "")
        return row_id == str(preserved_user.id) or username == preserved_user.username or email == preserved_user.email

    async def _restore_neo4j_dump(
        self,
        session: Neo4jSession,
        graph_path: Path,
        schema_path: Path | None,
    ) -> dict[str, int]:
        self._stage("loading Neo4j graph dump JSON")
        with open(graph_path, "r", encoding="utf-8") as f:
            graph_data = json.load(f)

        schema_data: dict[str, list[str]] = {"constraints": [], "indexes": []}
        if schema_path and schema_path.exists():
            self._stage("loading Neo4j schema dump JSON")
            with open(schema_path, "r", encoding="utf-8") as f:
                raw_schema = json.load(f)
                if isinstance(raw_schema, dict):
                    schema_data = {
                        "constraints": list(raw_schema.get("constraints", [])),
                        "indexes": list(raw_schema.get("indexes", [])),
                    }

        self._stage("wiping existing Neo4j graph")
        clear_summary = await self._wipe_neo4j_graph(session)

        self._stage("recreating Neo4j nodes from dump")
        nodes_started = time.perf_counter()
        id_mapping: dict[int, str] = {}
        nodes_by_labels: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for node in graph_data.get("nodes", []):
            labels = [lbl for lbl in node.get("labels", []) if isinstance(lbl, str)]
            valid_labels = [lbl for lbl in labels if all(c.isalnum() or c == "_" for c in lbl)]
            if not valid_labels:
                continue
            try:
                old_id = int(node.get("id"))
            except (TypeError, ValueError):
                continue
            label_key = tuple(valid_labels)
            nodes_by_labels.setdefault(label_key, []).append(
                {
                    "old_id": old_id,
                    "properties": node.get("properties", {}),
                }
            )

        node_batch_size = 1_000
        for labels, rows in nodes_by_labels.items():
            query = f"""
                UNWIND $rows AS row
                CREATE (n:{':'.join(labels)})
                SET n = row.properties
                RETURN row.old_id AS old_id, elementId(n) AS new_id
            """
            for offset in range(0, len(rows), node_batch_size):
                batch = rows[offset : offset + node_batch_size]
                result = await session.run(query, rows=batch)
                for rec in await result.data():
                    id_mapping[int(rec["old_id"])] = str(rec["new_id"])

        nodes_elapsed = time.perf_counter() - nodes_started
        self._stage(f"recreated {len(id_mapping)} Neo4j nodes in {nodes_elapsed:.2f}s")

        self._stage("recreating Neo4j relationships from dump")
        rels_started = time.perf_counter()
        restored_rels = 0
        rels_by_type: dict[str, list[dict[str, Any]]] = {}
        for rel in graph_data.get("relationships", []):
            try:
                start_source_id = int(rel.get("start_node_id", -1))
                end_source_id = int(rel.get("end_node_id", -1))
            except (TypeError, ValueError):
                continue
            start_id = id_mapping.get(start_source_id)
            end_id = id_mapping.get(end_source_id)
            rel_type = rel.get("type")
            if start_id is None or end_id is None or not isinstance(rel_type, str):
                continue
            if not all(c.isalnum() or c == "_" for c in rel_type):
                continue
            rels_by_type.setdefault(rel_type, []).append(
                {
                    "start_id": start_id,
                    "end_id": end_id,
                    "properties": rel.get("properties", {}),
                }
            )

        rel_batch_size = 5_000
        for rel_type, rows in rels_by_type.items():
            query = f"""
                UNWIND $rows AS row
                MATCH (a)
                WHERE elementId(a) = row.start_id
                MATCH (b)
                WHERE elementId(b) = row.end_id
                CREATE (a)-[r:{rel_type}]->(b)
                SET r = row.properties
                RETURN count(r) AS created
            """
            for offset in range(0, len(rows), rel_batch_size):
                batch = rows[offset : offset + rel_batch_size]
                result = await session.run(query, rows=batch)
                rec = await result.single()
                restored_rels += int(rec["created"]) if rec is not None else 0

        rels_elapsed = time.perf_counter() - rels_started
        self._stage(f"recreated {restored_rels} Neo4j relationships in {rels_elapsed:.2f}s")

        self._stage("restoring Neo4j constraints and indexes")
        restored_constraints = 0
        for statement in schema_data.get("constraints", []):
            try:
                await session.run(statement)
                restored_constraints += 1
            except Exception:
                pass

        restored_indexes = 0
        for statement in schema_data.get("indexes", []):
            try:
                await session.run(statement)
                restored_indexes += 1
            except Exception:
                pass

        return {
            "previous_nodes_removed": clear_summary["previous_nodes_removed"],
            "previous_relationships_removed": clear_summary["previous_relationships_removed"],
            "nodes": len(id_mapping),
            "relationships": restored_rels,
            "constraints": restored_constraints,
            "indexes": restored_indexes,
        }

    @staticmethod
    def _normalize_id_list(raw: Any) -> list[str]:
        if raw is None:
            return []
        if isinstance(raw, list):
            return [str(item) for item in raw if str(item).strip()]
        if isinstance(raw, tuple):
            return [str(item) for item in raw if str(item).strip()]
        raw_str = str(raw).strip()
        if not raw_str:
            return []
        try:
            loaded = json.loads(raw_str)
        except Exception:
            loaded = None
        if isinstance(loaded, list):
            return [str(item) for item in loaded if str(item).strip()]
        return [raw_str]

    @classmethod
    def _build_relations_json_for_event(cls, row: dict[str, Any]) -> str:
        relations_raw = row.get("relations_json")
        if isinstance(relations_raw, str) and relations_raw.strip():
            try:
                loaded = json.loads(relations_raw)
            except json.JSONDecodeError:
                loaded = None
            if isinstance(loaded, list):
                valid: list[dict[str, str]] = []
                for item in loaded:
                    if not isinstance(item, dict):
                        continue
                    relation_type = item.get("relation_type")
                    target_event_id = item.get("target_event_id")
                    if relation_type in {
                        "BEFORE",
                        "AFTER",
                        "DERIVED_FROM",
                        "RELATED_TO",
                    } and target_event_id:
                        valid.append(
                            {
                                "relation_type": str(relation_type),
                                "target_event_id": str(target_event_id),
                            }
                        )
                if valid:
                    return json.dumps(valid, ensure_ascii=False)

        fallback: list[dict[str, str]] = []
        for field_name, relation_type in (
            ("before_event_id", "BEFORE"),
            ("after_event_id", "AFTER"),
        ):
            target_event_id = row.get(field_name)
            if target_event_id:
                fallback.append(
                    {
                        "relation_type": relation_type,
                        "target_event_id": str(target_event_id),
                    }
                )
        return json.dumps(fallback, ensure_ascii=False)

    @classmethod
    def _build_event_node_payload(cls, row: dict[str, Any]) -> dict[str, Any]:
        event_id = str(
            row.get("event_id")
            or row.get("timeline_event_id")
            or row.get("entity_instance_id")
            or ""
        )
        title = str(
            row.get("title") or row.get("name") or row.get("alias") or event_id or ""
        )
        description = str(
            row.get("description")
            or row.get("text")
            or row.get("autogenerated_text")
            or ""
        )
        source = (
            row.get("source")
            or _extract_legacy_event_source(
                row.get("text_linked"),
                row.get("text"),
                row.get("autogenerated_text_linked"),
                row.get("autogenerated_text"),
                description,
            )
        )
        source_entity_id = row.get("source_entity_id") or row.get("created_from_entity_id")
        source_instance_id = row.get("source_instance_id") or row.get("created_from_instance_id")
        related_instance_ids = cls._normalize_id_list(
            row.get("related_instance_ids")
        )
        involves_entity_ids = cls._normalize_id_list(
            row.get("involves_entity_ids")
            or row.get("related_entity_ids")
            or row.get("involves_entity_edge_ids")
        )
        # Legacy timeline nodes can carry stale related_entity_ids while
        # related_instance_ids remain valid content links.
        related_entity_ids = related_instance_ids or involves_entity_ids
        return {
            "event_id": event_id,
            "instance_id": str(row.get("instance_id") or ""),
            "ontology_id": row.get("ontology_id"),
            "entity_instance_id": str(
                row.get("entity_instance_id") or event_id or ""
            ),
            "timeline_event_id": str(row.get("timeline_event_id") or event_id),
            "title": title,
            "name": str(row.get("name") or title),
            "alias": str(row.get("alias") or title),
            "description": description,
            "source": str(source) if source is not None else None,
            "created_from_instance_id": source_instance_id,
            "source_instance_id": source_instance_id,
            "created_from_entity_id": source_entity_id,
            "source_entity_id": source_entity_id,
            "related_instance_ids": related_instance_ids,
            "involves_entity_ids": involves_entity_ids,
            "related_entity_ids": related_entity_ids,
            "relations_json": cls._build_relations_json_for_event(row),
            "text_linked": row.get("text_linked") or row.get("text"),
            "autogenerated_text_linked": row.get("autogenerated_text_linked")
            or row.get("autogenerated_text"),
            "updated_at": row.get("updated_at")
            or row.get("last_updated_date")
            or row.get("created_at"),
        }

    async def _repair_graph_after_import(
        self,
        session: Neo4jSession,
    ) -> dict[str, int]:
        entity_nodes = await self._repair_entity_nodes(session)
        event_nodes = await self._repair_event_nodes(session)
        pdf_chunks = await self._repair_pdf_chunk_nodes(session)
        ontology_links = await self._repair_ontology_links(session)
        timeline_summary = await OntologyInstanceService(
            sql_session=None, graph_session=session
        ).rebuild_timeline_relationships()
        return {
            "entity_nodes_updated": entity_nodes["updated"],
            "entity_links_restored": entity_nodes["links_restored"],
            "event_nodes_updated": event_nodes["updated"],
            "event_links_restored": event_nodes["links_restored"],
            "pdf_chunk_nodes_updated": pdf_chunks["updated"],
            "ontology_nodes_synced": ontology_links["ontology_nodes_synced"],
            "ontology_instance_links": ontology_links["ontology_instance_links"],
            "timeline_events_processed": timeline_summary["processed_events"],
            "timeline_events_failed": timeline_summary["failed_events"],
        }

    async def _repair_pdf_chunk_nodes(self, session: Neo4jSession) -> dict[str, int]:
        """Backfill PdfChunk ontology_id values from imported LibraryItem records."""
        sm = get_sessionmaker()
        with sm() as sql_session:
            items = sql_session.execute(
                select(LibraryItem.id, LibraryItem.ontology_id)
            ).all()

        if not items:
            return {"updated": 0}

        payload = [
            {
                "library_item_id": str(item_id),
                "ontology_id": int(ontology_id),
            }
            for item_id, ontology_id in items
        ]

        result = await session.run(
            """
            UNWIND $payload AS item
            MATCH (chunk:PdfChunk)
            WHERE toString(chunk.library_item_id) = item.library_item_id
            SET chunk.ontology_id = item.ontology_id
            RETURN count(chunk) AS updated
            """,
            payload=payload,
        )
        record = await result.single()
        return {"updated": int(record["updated"] if record else 0)}

    async def _repair_entity_nodes(self, session: Neo4jSession) -> dict[str, int]:
        result = await session.run(
            """
            MATCH (entity:EntityInstance)
            OPTIONAL MATCH (inst:OntologyInstance)-[:HAS_ENTITY]->(entity)
            RETURN entity.entity_instance_id AS entity_id,
                   entity.instance_id AS instance_id,
                   entity.ontology_id AS ontology_id,
                   entity.properties AS properties,
                   entity.text AS text,
                   entity.text_linked AS text_linked,
                   entity.autogenerated_text AS autogenerated_text,
                   entity.autogenerated_text_linked AS autogenerated_text_linked,
                   inst.instance_id AS linked_instance_id,
                   inst.ontology_id AS linked_ontology_id
            """
        )
        rows = await result.data()
        payload: list[dict[str, Any]] = []
        links_restored = 0
        for row in rows:
            instance_id = row.get("linked_instance_id") or row.get("instance_id")
            entity_id = row.get("entity_id")
            if not entity_id or not instance_id:
                continue
            ontology_id = row.get("linked_ontology_id") or row.get("ontology_id")
            if row.get("linked_instance_id") is None:
                links_restored += 1
            payload.append(
                {
                    "entity_id": str(entity_id),
                    "instance_id": str(instance_id),
                    "ontology_id": ontology_id,
                    "properties": row.get("properties") or "{}",
                    "text_linked": row.get("text_linked") or row.get("text"),
                    "autogenerated_text_linked": row.get("autogenerated_text_linked")
                    or row.get("autogenerated_text"),
                }
            )

        if payload:
            await session.run(
                """
                UNWIND $payload AS item
                MATCH (entity:EntityInstance {entity_instance_id: item.entity_id})
                MATCH (inst:OntologyInstance {instance_id: item.instance_id})
                MERGE (inst)-[:HAS_ENTITY]->(entity)
                SET entity.instance_id = item.instance_id,
                    entity.ontology_id = item.ontology_id,
                    entity.properties = item.properties,
                    entity.text_linked = item.text_linked,
                    entity.autogenerated_text_linked = item.autogenerated_text_linked
                """,
                payload=payload,
            )
        return {"updated": len(payload), "links_restored": links_restored}

    async def _repair_event_nodes(self, session: Neo4jSession) -> dict[str, int]:
        entity_ids_by_instance = await self._entity_ids_by_instance(session)
        await session.run(
            """
            MATCH (event:TimelineEvent)
            SET event:Event
            """
        )
        await session.run(
            """
            MATCH (event:Event)
            WHERE coalesce(toString(event.event_id), '') = ''
              AND coalesce(toString(event.entity_instance_id), '') <> ''
            SET event.event_id = toString(event.entity_instance_id)
            """
        )

        result = await session.run(
            """
            MATCH (event:Event)
             OPTIONAL MATCH (inst:OntologyInstance)-[:HAS_EVENT|HAS_TIMELINE_EVENT]->(event)
                        OPTIONAL MATCH (event)-[:SOURCE_ENTITY]->(source_entity)
                        OPTIONAL MATCH (event)-[:INVOLVES_ENTITY]->(related_entity)
            RETURN event.event_id AS event_id,
                 properties(event)['timeline_event_id'] AS timeline_event_id,
                   event.instance_id AS instance_id,
                   event.ontology_id AS ontology_id,
                   event.entity_instance_id AS entity_instance_id,
                   event.title AS title,
                   event.name AS name,
                   event.alias AS alias,
                   event.description AS description,
                   properties(event)['source'] AS source,
                   event.text AS text,
                   event.text_linked AS text_linked,
                   event.autogenerated_text AS autogenerated_text,
                   event.autogenerated_text_linked AS autogenerated_text_linked,
                   properties(event)['source_entity_id'] AS source_entity_id,
                   properties(event)['created_from_entity_id'] AS created_from_entity_id,
                   properties(event)['source_instance_id'] AS source_instance_id,
                   properties(event)['created_from_instance_id'] AS created_from_instance_id,
                   properties(event)['related_instance_ids'] AS related_instance_ids,
                   properties(event)['involves_entity_ids'] AS involves_entity_ids,
                   properties(event)['related_entity_ids'] AS related_entity_ids,
                   properties(event)['relations_json'] AS relations_json,
                   properties(event)['before_event_id'] AS before_event_id,
                   properties(event)['after_event_id'] AS after_event_id,
                   event.updated_at AS updated_at,
                   event.last_updated_date AS last_updated_date,
                   event.created_at AS created_at,
                   inst.instance_id AS linked_instance_id,
                   inst.ontology_id AS linked_ontology_id,
                   properties(source_entity)['entity_instance_id'] AS source_entity_edge_id,
                   collect(DISTINCT properties(related_entity)['entity_instance_id']) AS involves_entity_edge_ids
            """
        )
        rows = await result.data()
        payload: list[dict[str, Any]] = []
        links_restored = 0
        for row in rows:
            instance_id = (
                row.get("linked_instance_id")
                or row.get("instance_id")
                or row.get("created_from_instance_id")
                or row.get("source_instance_id")
            )
            event_id = row.get("event_id") or row.get("entity_instance_id")
            if not event_id or not instance_id:
                continue

            source_instance_id = (
                row.get("source_instance_id") or row.get("created_from_instance_id")
            )
            canonical_instance_id = str(instance_id)
            valid_entity_ids = set(entity_ids_by_instance.get(canonical_instance_id, []))

            raw_source_entity_id = (
                row.get("source_entity_edge_id")
                or row.get("source_entity_id")
                or row.get("created_from_entity_id")
            )
            source_entity_id = str(raw_source_entity_id) if raw_source_entity_id else None
            if source_entity_id and source_entity_id not in valid_entity_ids:
                source_entity_id = None

            if not source_entity_id and source_instance_id:
                source_candidates = entity_ids_by_instance.get(str(source_instance_id), [])
                if len(source_candidates) == 1:
                    source_entity_id = source_candidates[0]

            involves_candidates = self._normalize_id_list(
                row.get("involves_entity_edge_ids")
                or row.get("involves_entity_ids")
                or row.get("related_entity_ids")
            )
            involves_entity_ids = [
                candidate
                for candidate in involves_candidates
                if candidate in valid_entity_ids
            ]

            normalized = self._build_event_node_payload(
                {
                    **row,
                    "event_id": event_id,
                    "timeline_event_id": row.get("timeline_event_id") or event_id,
                    "instance_id": instance_id,
                    "ontology_id": row.get("linked_ontology_id") or row.get("ontology_id"),
                    "source_instance_id": source_instance_id,
                    "source_entity_id": source_entity_id,
                    "involves_entity_ids": involves_entity_ids,
                    "related_entity_ids": involves_entity_ids,
                }
            )
            if row.get("linked_instance_id") is None:
                links_restored += 1
            payload.append(normalized)

        if payload:
            await session.run(
                """
                UNWIND $payload AS item
                MATCH (event:Event {event_id: item.event_id})
                MATCH (inst:OntologyInstance {instance_id: item.instance_id})
                MERGE (inst)-[:HAS_EVENT]->(event)
                SET event.event_id = item.event_id,
                    event.timeline_event_id = item.timeline_event_id,
                    event.instance_id = item.instance_id,
                    event.ontology_id = item.ontology_id,
                    event.entity_instance_id = item.entity_instance_id,
                    event.title = item.title,
                    event.name = item.name,
                    event.alias = item.alias,
                    event.description = item.description,
                    event.source = item.source,
                    event.created_from_instance_id = item.created_from_instance_id,
                    event.source_instance_id = item.source_instance_id,
                    event.created_from_entity_id = item.created_from_entity_id,
                    event.source_entity_id = item.source_entity_id,
                    event.related_instance_ids = item.related_instance_ids,
                    event.involves_entity_ids = item.involves_entity_ids,
                    event.related_entity_ids = item.related_entity_ids,
                    event.relations_json = item.relations_json,
                    event.text_linked = item.text_linked,
                    event.autogenerated_text_linked = item.autogenerated_text_linked,
                    event.updated_at = item.updated_at
                """,
                payload=payload,
            )
        return {"updated": len(payload), "links_restored": links_restored}

    async def _entity_ids_by_instance(self, session: Neo4jSession) -> dict[str, list[str]]:
        result = await session.run(
            """
            MATCH (inst:OntologyInstance)-[:HAS_ENTITY]->(entity:EntityInstance)
            RETURN inst.instance_id AS instance_id,
                   collect(DISTINCT entity.entity_instance_id) AS entity_ids
            """
        )
        rows = await result.data()
        mapping: dict[str, list[str]] = {}
        for row in rows:
            instance_id = row.get("instance_id")
            if not instance_id:
                continue
            ids = [
                str(value)
                for value in (row.get("entity_ids") or [])
                if value is not None and str(value).strip()
            ]
            if ids:
                mapping[str(instance_id)] = sorted(set(ids))
        return mapping

    async def _repair_ontology_links(self, session: Neo4jSession) -> dict[str, int]:
        sm = get_sessionmaker()
        with sm() as sql_session:
            ontologies = sql_session.execute(select(Ontology).order_by(Ontology.id.asc())).scalars().all()

        payload = [{"ontology_id": ontology.id, "name": ontology.name} for ontology in ontologies]
        if payload:
            await session.run(
                """
                UNWIND $payload AS item
                MERGE (ontology:Ontology {ontology_id: item.ontology_id})
                SET ontology.name = item.name
                """,
                payload=payload,
            )
            await session.run(
                """
                MATCH (ontology:Ontology)
                MATCH (inst:OntologyInstance)
                WHERE toInteger(inst.ontology_id) = toInteger(ontology.ontology_id)
                MERGE (ontology)-[:HAS_INSTANCE]->(inst)
                """
            )
        count_result = await session.run(
            """
            MATCH (:Ontology)-[rel:HAS_INSTANCE]->(:OntologyInstance)
            RETURN count(rel) AS count
            """
        )
        count_row = await count_result.single()
        link_count = int(count_row["count"]) if count_row is not None else 0
        return {
            "ontology_nodes_synced": len(payload),
            "ontology_instance_links": link_count,
        }

    def _stage(self, message: str) -> None:
        text = f"[legacy-import][shrecknet] {message}"
        print(text, flush=True)
        logger.info(text)

    async def _wipe_neo4j_graph(self, session: Neo4jSession, *, batch_size: int = 5_000) -> dict[str, int]:
        node_count_result = await session.run("MATCH (n) RETURN count(n) AS count")
        node_count_record = await node_count_result.single()
        previous_nodes = int(node_count_record["count"]) if node_count_record is not None else 0

        rel_count_result = await session.run("MATCH ()-[r]->() RETURN count(r) AS count")
        rel_count_record = await rel_count_result.single()
        previous_relationships = int(rel_count_record["count"]) if rel_count_record is not None else 0

        while True:
            delete_result = await session.run(
                """
                MATCH (n)
                WITH n LIMIT $batch_size
                DETACH DELETE n
                RETURN count(n) AS deleted
                """,
                batch_size=batch_size,
            )
            delete_record = await delete_result.single()
            deleted = int(delete_record["deleted"]) if delete_record is not None else 0
            if deleted == 0:
                break

        remaining_result = await session.run("MATCH (n) RETURN count(n) AS remaining")
        remaining_record = await remaining_result.single()
        remaining = int(remaining_record["remaining"]) if remaining_record is not None else -1
        if remaining != 0:
            raise ValueError(
                f"Failed to clear existing Neo4j graph before import. Remaining nodes: {remaining}"
            )

        return {
            "previous_nodes_removed": previous_nodes,
            "previous_relationships_removed": previous_relationships,
        }

    def _resolve_data_dir(self) -> Path:
        db_path = self._sqlite_url_to_path(self.settings.database_url)
        jobs_path = self._sqlite_url_to_path(self.settings.jobs_database_url)
        candidates = [db_path.parent, jobs_path.parent]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]

    def _sqlite_url_to_path(self, url: str) -> Path:
        prefixes = ("sqlite+aiosqlite:///", "sqlite:///")
        for prefix in prefixes:
            if url.startswith(prefix):
                raw = url[len(prefix) :]
                if raw.startswith("./"):
                    return Path(raw[2:])
                if raw.startswith("/"):
                    return Path(raw)
                return Path(raw)
        raise ValueError(f"Unsupported sqlite URL for backup: {url}")
