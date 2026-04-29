"""
Backup and restore service for backend_2.

Full-system backup captures:
- Local SQLite datasets from configured data directory
- Neo4j logical export (schema + graph data)
- Media files (excluding backup folders)

Backups are written to media/backups/download.
Uploaded archives are stored in media/backups/upload.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import shutil
import sqlite3
import tarfile
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo4j import AsyncSession as Neo4jSession

from app.core.config_store import get_settings
from app.db.jobs_session import get_jobs_engine
from app.db.session import get_engine

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, float, str], Awaitable[None] | None]


class BackupService:
    """Service for creating and restoring full-system backups."""

    MANIFEST_VERSION = 2

    def __init__(self):
        self.settings = get_settings()
        self.media_root = Path(self.settings.media_root)
        self.backup_dir = self.media_root / "backups"
        self.download_backup_dir = self.backup_dir / "download"
        self.uploaded_backup_dir = self.backup_dir / "upload"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.media_root.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.download_backup_dir.mkdir(parents=True, exist_ok=True)
        self.uploaded_backup_dir.mkdir(parents=True, exist_ok=True)

    async def create_backup(
        self,
        neo4j_session: Neo4jSession,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Create a full-system backup archive."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_name = f"full_backup_{timestamp}"
        staging_root = Path("/tmp") / backup_name

        await self._report(progress_callback, "staging", 0.05, "Preparing backup staging")
        if staging_root.exists():
            shutil.rmtree(staging_root)
        staging_root.mkdir(parents=True, exist_ok=True)

        package_root = staging_root / backup_name
        package_root.mkdir(parents=True, exist_ok=True)

        try:
            # Databases
            await self._report(
                progress_callback,
                "sqlite_copy",
                0.2,
                "Copying SQLite datasets",
            )
            db_summary = self._snapshot_databases(package_root / "databases")

            # Neo4j logical export
            await self._report(
                progress_callback,
                "neo4j_dump",
                0.45,
                "Exporting Neo4j logical dump",
            )
            neo4j_summary = await self._export_neo4j_dump(neo4j_session, package_root / "neo4j")

            # Media copy
            await self._report(
                progress_callback,
                "media_copy",
                0.65,
                "Copying media files",
            )
            media_summary = self._copy_media(package_root / "media")

            # Manifest + checksums
            await self._report(
                progress_callback,
                "manifest",
                0.8,
                "Building manifest and checksums",
            )
            manifest = self._build_manifest(package_root, timestamp, db_summary, neo4j_summary, media_summary)
            manifest_path = package_root / "manifest.json"
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)

            # Archive creation (atomic move)
            await self._report(
                progress_callback,
                "archive",
                0.9,
                "Creating compressed archive",
            )
            final_archive = self.download_backup_dir / f"{backup_name}.tar.gz"
            temp_archive = self.download_backup_dir / f"{backup_name}.tar.gz.tmp"

            with tarfile.open(temp_archive, "w:gz") as tar:
                tar.add(package_root, arcname=backup_name)

            temp_archive.replace(final_archive)

            return {
                "backup_kind": "full_system",
                "filename": final_archive.name,
                "path": str(final_archive),
                "storage_path": str(final_archive),
                "size_bytes": final_archive.stat().st_size,
                "created_at": timestamp,
                "database_files": db_summary["files"],
                "neo4j_nodes": neo4j_summary["nodes"],
                "neo4j_relationships": neo4j_summary["relationships"],
                "media_files": media_summary["files"],
            }
        finally:
            if staging_root.exists():
                shutil.rmtree(staging_root)

    async def restore_backup(
        self,
        backup_path: Path,
        neo4j_session: Neo4jSession,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Destructively restore a full-system backup archive."""
        if not backup_path.exists():
            candidate = self.uploaded_backup_dir / backup_path.name
            if candidate.exists():
                backup_path = candidate
            else:
                raise FileNotFoundError(f"Backup file not found: {backup_path}")

        await self._report(progress_callback, "validating", 0.1, "Validating backup archive")

        temp_extract_dir = Path("/tmp") / f"restore_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        temp_extract_dir.mkdir(parents=True, exist_ok=True)

        try:
            with tarfile.open(backup_path, "r:gz") as tar:
                self._safe_extract_tar(tar, temp_extract_dir)

            package_dirs = [p for p in temp_extract_dir.iterdir() if p.is_dir()]
            if len(package_dirs) != 1:
                raise ValueError("Invalid backup archive structure: expected single root directory")

            package_root = package_dirs[0]
            manifest = self._validate_manifest(package_root)
            self._validate_manifest_checksums(package_root, manifest)

            await self._prepare_sqlite_restore()
            await self._report(progress_callback, "sqlite_restore", 0.3, "Restoring SQLite datasets")
            db_restore_summary = self._restore_databases(package_root / "databases")

            await self._report(progress_callback, "media_restore", 0.55, "Restoring media files")
            media_restore_summary = self._restore_media(package_root / "media")

            await self._report(progress_callback, "neo4j_import", 0.75, "Restoring Neo4j logical dump")
            neo4j_restore_summary = await self._restore_neo4j_dump(
                neo4j_session,
                package_root / "neo4j" / "graph.json",
                package_root / "neo4j" / "schema.json",
            )

            await self._report(progress_callback, "finalizing", 0.95, "Finalizing restore")

            return {
                "status": "success",
                "backup_kind": "full_system",
                "restored_at": datetime.now(timezone.utc).isoformat(),
                "restart_required": True,
                "backup_metadata": {
                    "manifest_version": manifest.get("version"),
                    "created_at": manifest.get("created_at"),
                },
                "restore_summary": {
                    "database_files": db_restore_summary,
                    "media_files": media_restore_summary,
                    "neo4j": neo4j_restore_summary,
                },
            }
        finally:
            if temp_extract_dir.exists():
                shutil.rmtree(temp_extract_dir)

    async def _prepare_sqlite_restore(self) -> None:
        """Close shared SQLAlchemy engines so SQLite files can be replaced safely."""
        main_dispose = get_engine().dispose()
        if inspect.isawaitable(main_dispose):
            await main_dispose

        jobs_dispose = get_jobs_engine().dispose()
        if inspect.isawaitable(jobs_dispose):
            await jobs_dispose

    async def _report(
        self,
        callback: ProgressCallback | None,
        phase: str,
        progress: float,
        status: str,
    ) -> None:
        if not callback:
            return
        result = callback(phase, progress, status)
        if result is not None:
            await result

    def _snapshot_databases(self, destination_dir: Path) -> dict[str, Any]:
        destination_dir.mkdir(parents=True, exist_ok=True)
        data_dir = self._resolve_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)

        copied_files: list[str] = []
        for db_file in sorted(data_dir.glob("*.db")):
            target = destination_dir / db_file.name
            self._sqlite_backup_file(db_file, target)
            copied_files.append(db_file.name)

        logger.info("Copied %d SQLite database files", len(copied_files))
        return {"files": copied_files, "source": str(data_dir)}

    def _sqlite_backup_file(self, source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(source.as_posix()) as source_conn:
            with sqlite3.connect(target.as_posix()) as target_conn:
                source_conn.backup(target_conn)

    async def _export_neo4j_dump(
        self,
        session: Neo4jSession,
        destination_dir: Path,
    ) -> dict[str, int]:
        destination_dir.mkdir(parents=True, exist_ok=True)

        nodes_result = await session.run(
            """
            MATCH (n)
            RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS properties
            """
        )
        nodes = await nodes_result.data()

        rels_result = await session.run(
            """
            MATCH (a)-[r]->(b)
            RETURN elementId(r) AS id,
                   elementId(a) AS start_node_id,
                   elementId(b) AS end_node_id,
                   type(r) AS type,
                   properties(r) AS properties
            """
        )
        relationships = await rels_result.data()

        graph_path = destination_dir / "graph.json"
        with open(graph_path, "w", encoding="utf-8") as f:
            json.dump(
                {"nodes": nodes, "relationships": relationships},
                f,
                indent=2,
                default=self._json_default,
            )

        schema_dump = await self._export_neo4j_schema(session)
        schema_path = destination_dir / "schema.json"
        with open(schema_path, "w", encoding="utf-8") as f:
            json.dump(schema_dump, f, indent=2, default=self._json_default)

        return {
            "nodes": len(nodes),
            "relationships": len(relationships),
            "constraints": len(schema_dump.get("constraints", [])),
            "indexes": len(schema_dump.get("indexes", [])),
        }

    async def _export_neo4j_schema(self, session: Neo4jSession) -> dict[str, list[str]]:
        constraints: list[str] = []
        indexes: list[str] = []

        try:
            constraint_result = await session.run(
                "SHOW CONSTRAINTS YIELD createStatement RETURN createStatement"
            )
            constraints = [
                row["createStatement"]
                for row in await constraint_result.data()
                if row.get("createStatement")
            ]
        except Exception as exc:  # pragma: no cover
            logger.warning("Could not export Neo4j constraints: %s", exc)

        try:
            index_result = await session.run(
                "SHOW INDEXES YIELD createStatement RETURN createStatement"
            )
            indexes = [
                row["createStatement"]
                for row in await index_result.data()
                if row.get("createStatement")
            ]
        except Exception as exc:  # pragma: no cover
            logger.warning("Could not export Neo4j indexes: %s", exc)

        return {"constraints": constraints, "indexes": indexes}

    def _copy_media(self, destination_dir: Path) -> dict[str, int]:
        destination_dir.mkdir(parents=True, exist_ok=True)
        if not self.media_root.exists():
            return {"files": 0}

        for item in self.media_root.iterdir():
            if item.name == "backups":
                continue
            dest = destination_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

        files = sum(1 for p in destination_dir.rglob("*") if p.is_file())
        return {"files": files}

    def _build_manifest(
        self,
        package_root: Path,
        timestamp: str,
        db_summary: dict[str, Any],
        neo4j_summary: dict[str, Any],
        media_summary: dict[str, Any],
    ) -> dict[str, Any]:
        files: list[dict[str, Any]] = []
        for file_path in sorted(p for p in package_root.rglob("*") if p.is_file()):
            rel = file_path.relative_to(package_root).as_posix()
            files.append(
                {
                    "path": rel,
                    "size_bytes": file_path.stat().st_size,
                    "sha256": self._sha256(file_path),
                }
            )

        return {
            "version": self.MANIFEST_VERSION,
            "backup_kind": "full_system",
            "created_at": timestamp,
            "components": {
                "databases": db_summary,
                "neo4j": neo4j_summary,
                "media": media_summary,
            },
            "restore_instructions": {
                "destructive": True,
                "restart_required": True,
            },
            "files": files,
        }

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _json_default(value: Any) -> str:
        """Serialize non-standard values (e.g. Neo4j temporal types) to strings."""
        if hasattr(value, "isoformat"):
            return value.isoformat()  # datetime/date/time
        if hasattr(value, "iso_format"):
            return value.iso_format()  # Neo4j temporal values
        return str(value)

    def _safe_extract_tar(self, tar: tarfile.TarFile, destination: Path) -> None:
        dest_resolved = destination.resolve()
        for member in tar.getmembers():
            member_path = destination / member.name
            resolved_member = member_path.resolve()
            if dest_resolved not in resolved_member.parents and resolved_member != dest_resolved:
                raise ValueError(f"Unsafe archive entry detected: {member.name}")
        tar.extractall(destination)

    def _validate_manifest(self, package_root: Path) -> dict[str, Any]:
        manifest_path = package_root / "manifest.json"
        if not manifest_path.exists():
            raise ValueError("Invalid backup archive: missing manifest.json")

        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        required_keys = {"version", "backup_kind", "files", "components"}
        missing = [key for key in required_keys if key not in manifest]
        if missing:
            raise ValueError(f"Invalid backup manifest: missing keys {missing}")

        if manifest.get("backup_kind") != "full_system":
            raise ValueError("Unsupported backup kind")

        return manifest

    def _validate_manifest_checksums(self, package_root: Path, manifest: dict[str, Any]) -> None:
        entries = manifest.get("files", [])
        if not isinstance(entries, list):
            raise ValueError("Invalid backup manifest: files must be a list")

        for entry in entries:
            rel_path = entry.get("path")
            expected = entry.get("sha256")
            if not rel_path or not expected:
                raise ValueError("Invalid backup manifest: malformed file entry")

            file_path = package_root / rel_path
            if not file_path.exists():
                raise ValueError(f"Invalid backup archive: missing file {rel_path}")

            actual = self._sha256(file_path)
            if actual != expected:
                raise ValueError(f"Checksum mismatch for {rel_path}")

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

    def _restore_databases(self, source_dir: Path) -> int:
        if not source_dir.exists():
            raise ValueError("Invalid backup archive: missing databases directory")

        data_dir = self._resolve_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        jobs_db_name = self._sqlite_url_to_path(self.settings.jobs_database_url).name

        for existing in data_dir.glob("*.db"):
            if existing.name == jobs_db_name:
                continue
            existing.unlink()

        restored = 0
        for db_file in sorted(source_dir.glob("*.db")):
            if db_file.name == jobs_db_name:
                logger.info(
                    "Skipping restore for active jobs database file %s to preserve job visibility",
                    jobs_db_name,
                )
                continue
            shutil.copy2(db_file, data_dir / db_file.name)
            restored += 1

        return restored

    def _restore_media(self, source_dir: Path) -> int:
        if not source_dir.exists():
            return 0

        self.media_root.mkdir(parents=True, exist_ok=True)

        for item in self.media_root.iterdir():
            if item.name == "backups":
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

        for item in source_dir.iterdir():
            dest = self.media_root / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

        return sum(1 for p in source_dir.rglob("*") if p.is_file())

    async def _restore_neo4j_dump(
        self,
        session: Neo4jSession,
        graph_path: Path,
        schema_path: Path,
    ) -> dict[str, int]:
        if not graph_path.exists():
            raise ValueError("Invalid backup archive: missing neo4j/graph.json")

        with open(graph_path, "r", encoding="utf-8") as f:
            graph_data = json.load(f)

        schema_data: dict[str, list[str]] = {"constraints": [], "indexes": []}
        if schema_path.exists():
            with open(schema_path, "r", encoding="utf-8") as f:
                raw_schema = json.load(f)
                if isinstance(raw_schema, dict):
                    schema_data = {
                        "constraints": list(raw_schema.get("constraints", [])),
                        "indexes": list(raw_schema.get("indexes", [])),
                    }

        clear_summary = await self._wipe_neo4j_graph(session)

        id_mapping: dict[str, str] = {}
        for node in graph_data.get("nodes", []):
            labels = [lbl for lbl in node.get("labels", []) if isinstance(lbl, str)]
            valid_labels = [lbl for lbl in labels if all(c.isalnum() or c == "_" for c in lbl)]
            if not valid_labels:
                continue

            query = f"CREATE (n:{':'.join(valid_labels)}) SET n = $properties RETURN elementId(n) AS new_id"
            result = await session.run(query, properties=node.get("properties", {}))
            rec = await result.single()
            if rec is None:
                continue
            old_id = node.get("id")
            if isinstance(old_id, str):
                id_mapping[old_id] = str(rec["new_id"])

        restored_rels = 0
        for rel in graph_data.get("relationships", []):
            start_raw = rel.get("start_node_id")
            end_raw = rel.get("end_node_id")
            start_id = id_mapping.get(start_raw) if isinstance(start_raw, str) else None
            end_id = id_mapping.get(end_raw) if isinstance(end_raw, str) else None
            rel_type = rel.get("type")
            if start_id is None or end_id is None or not isinstance(rel_type, str):
                continue
            if not all(c.isalnum() or c == "_" for c in rel_type):
                continue

            await session.run(
                f"""
                MATCH (a) WHERE elementId(a) = $start_id
                MATCH (b) WHERE elementId(b) = $end_id
                CREATE (a)-[r:{rel_type}]->(b)
                SET r = $properties
                """,
                start_id=start_id,
                end_id=end_id,
                properties=rel.get("properties", {}),
            )
            restored_rels += 1

        restored_constraints = 0
        for statement in schema_data.get("constraints", []):
            try:
                await session.run(statement)
                restored_constraints += 1
            except Exception:
                logger.warning("Skipping failing Neo4j constraint statement: %s", statement)

        restored_indexes = 0
        for statement in schema_data.get("indexes", []):
            try:
                await session.run(statement)
                restored_indexes += 1
            except Exception:
                logger.warning("Skipping failing Neo4j index statement: %s", statement)

        return {
            "previous_nodes_removed": clear_summary["previous_nodes_removed"],
            "previous_relationships_removed": clear_summary["previous_relationships_removed"],
            "nodes": len(id_mapping),
            "relationships": restored_rels,
            "constraints": restored_constraints,
            "indexes": restored_indexes,
        }

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
                f"Failed to clear existing Neo4j graph before restore. Remaining nodes: {remaining}"
            )

        return {
            "previous_nodes_removed": previous_nodes,
            "previous_relationships_removed": previous_relationships,
        }

    def get_uploaded_backup_path(self, filename: str) -> Path:
        safe_name = Path(filename).name
        return self.uploaded_backup_dir / safe_name

    def list_backups(self) -> list[dict[str, Any]]:
        backups: list[dict[str, Any]] = []
        if not self.download_backup_dir.exists():
            return backups

        for backup_file in sorted(self.download_backup_dir.glob("*.tar.gz"), reverse=True):
            backups.append(
                {
                    "backup_kind": "full_system",
                    "filename": backup_file.name,
                    "path": str(backup_file),
                    "storage_path": str(backup_file),
                    "size_bytes": backup_file.stat().st_size,
                    "created_at": datetime.fromtimestamp(
                        backup_file.stat().st_mtime, tz=timezone.utc
                    ).isoformat(),
                }
            )

        return backups

    def get_backup_path(self, filename: str) -> Path:
        backup_path = self.download_backup_dir / Path(filename).name
        if not backup_path.exists():
            raise FileNotFoundError(f"Backup not found: {filename}")
        return backup_path
