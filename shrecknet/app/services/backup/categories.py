"""Backup category registry: what can be backed up, how big it is, and how to export/restore it."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from app.core.config_store import CONFIG_DB_FILENAME, LEGACY_CONFIG_DB_FILENAME, Settings, _default_data_dir, get_settings
from app.services.backup.fileops import (
    SQLITE_SIDECAR_SUFFIXES,
    FileRecord,
    copy_tree_into,
    remove_path,
    sha256_file,
    swap_in,
    tree_size,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, float, str], Awaitable[None] | None]


@dataclass
class BackupContext:
    """Runtime collaborators handed to categories; everything is optional for sizing."""

    neo4j_session: Any | None = None
    previous_records: dict[str, FileRecord] = field(default_factory=dict)
    previous_root: Path | None = None
    should_stop: Callable[[], bool] | None = None
    dispose_engines: Callable[[], Awaitable[None]] | None = None
    options: dict[str, Any] = field(default_factory=dict)
    # Reports a sub-step to the job; fraction is the progress through the current category, if known.
    note: Callable[[str, float | None], Awaitable[None]] | None = None

    async def report(self, text: str, fraction: float | None = None) -> None:
        if self.note is not None:
            await self.note(text, fraction)


@dataclass(frozen=True)
class Estimate:
    size_bytes: int
    file_count: int
    approximate: bool = False


@dataclass
class ExportResult:
    records: list[FileRecord]
    details: dict[str, Any] = field(default_factory=dict)


class BackupCategory(ABC):
    id: str
    label: str
    description: str
    owner: str
    sensitive: bool = False
    default_selected: bool = True
    restart_required: bool = False

    def unavailable_reason(self) -> str | None:
        return None

    @abstractmethod
    async def estimate(self, ctx: BackupContext) -> Estimate: ...

    @abstractmethod
    async def export(self, destination: Path, ctx: BackupContext) -> ExportResult: ...

    @abstractmethod
    async def restore(self, source: Path, ctx: BackupContext) -> dict[str, Any]: ...


@dataclass(frozen=True)
class FileSource:
    """A directory whose selected top-level entries belong to a category.

    ``label`` is the folder name used inside the snapshot, so the same snapshot
    layout can be replayed onto ``root`` without any path translation.
    """

    label: str
    root: Path
    select: Callable[[Path], bool]

    def entries(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return [entry for entry in sorted(self.root.iterdir()) if _selectable(entry) and self.select(entry)]


def _selectable(entry: Path) -> bool:
    return not entry.name.startswith(".")


def names(*wanted: str) -> Callable[[Path], bool]:
    allowed = frozenset(wanted)
    return lambda entry: entry.name in allowed


def sqlite_files(*wanted: str) -> Callable[[Path], bool]:
    allowed = frozenset(wanted)
    return lambda entry: entry.is_file() and entry.name in allowed


class FileCategory(BackupCategory):
    def __init__(
        self,
        *,
        id: str,
        label: str,
        description: str,
        owner: str,
        sources: Callable[[], list[FileSource]],
        sensitive: bool = False,
        default_selected: bool = True,
        restart_required: bool = False,
        requires: Callable[[], str | None] | None = None,
    ) -> None:
        self.id = id
        self.label = label
        self.description = description
        self.owner = owner
        self.sensitive = sensitive
        self.default_selected = default_selected
        self.restart_required = restart_required
        self._sources = sources
        self._requires = requires

    def unavailable_reason(self) -> str | None:
        return self._requires() if self._requires else None

    async def estimate(self, ctx: BackupContext) -> Estimate:
        def measure() -> Estimate:
            total = 0
            count = 0
            for source in self._sources():
                for entry in source.entries():
                    size, files = tree_size(entry)
                    total += size
                    count += files
            return Estimate(total, count)

        return await asyncio.to_thread(measure)

    async def export(self, destination: Path, ctx: BackupContext) -> ExportResult:
        def run() -> ExportResult:
            records: list[FileRecord] = []
            for source in self._sources():
                for entry in source.entries():
                    records.extend(
                        copy_tree_into(
                            entry,
                            destination,
                            f"{source.label}/{entry.name}",
                            previous=ctx.previous_records,
                            previous_root=ctx.previous_root,
                            should_stop=ctx.should_stop,
                        )
                    )
            return ExportResult(records)

        destination.mkdir(parents=True, exist_ok=True)
        return await asyncio.to_thread(run)

    async def restore(self, source: Path, ctx: BackupContext) -> dict[str, Any]:
        if ctx.dispose_engines is not None:
            await ctx.dispose_engines()

        def run() -> dict[str, Any]:
            restored = 0
            removed = 0
            for file_source in self._sources():
                snapshot_dir = source / file_source.label
                incoming = (
                    {entry.name: entry for entry in sorted(snapshot_dir.iterdir())} if snapshot_dir.is_dir() else {}
                )
                for live in file_source.entries():
                    if live.name not in incoming:
                        remove_path(live)
                        for suffix in SQLITE_SIDECAR_SUFFIXES:
                            Path(f"{live}{suffix}").unlink(missing_ok=True)
                        removed += 1
                for name, entry in incoming.items():
                    swap_in(entry, file_source.root / name)
                    restored += 1
            return {"restored_entries": restored, "removed_entries": removed}

        return await asyncio.to_thread(run)


class Neo4jGraphCategory(BackupCategory):
    id = "graph"
    label = "Knowledge graph (Neo4j)"
    description = "Neo4j nodes, relationships, constraints and indexes as a logical dump."
    owner = "shrecknet"
    restart_required = False
    BYTES_PER_ELEMENT = 400

    async def estimate(self, ctx: BackupContext) -> Estimate:
        if ctx.neo4j_session is None:
            return Estimate(0, 0, approximate=True)
        try:
            nodes = await _single_count(ctx.neo4j_session, "MATCH (n) RETURN count(n) AS c")
            rels = await _single_count(ctx.neo4j_session, "MATCH ()-[r]->() RETURN count(r) AS c")
        except Exception as exc:  # sizing must never break the category list
            logger.warning("Could not estimate Neo4j size: %s", exc)
            return Estimate(0, 0, approximate=True)
        return Estimate((nodes + rels) * self.BYTES_PER_ELEMENT, 2, approximate=True)

    async def export(self, destination: Path, ctx: BackupContext) -> ExportResult:
        from app.services.backup_service import BackupService

        if ctx.neo4j_session is None:
            raise RuntimeError("Neo4j session is required to back up the graph")
        service = BackupService()
        destination.mkdir(parents=True, exist_ok=True)
        # Same graph.json layout as the legacy dump, streamed so large graphs never sit in memory.
        counts = {"nodes": 0, "relationships": 0}
        queries = {
            "nodes": (
                "MATCH (n) RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS properties",
                ("id", "labels", "properties"),
            ),
            "relationships": (
                "MATCH (a)-[r]->(b) RETURN elementId(r) AS id, elementId(a) AS start_node_id, "
                "elementId(b) AS end_node_id, type(r) AS type, properties(r) AS properties",
                ("id", "start_node_id", "end_node_id", "type", "properties"),
            ),
        }
        try:
            totals = {
                "nodes": await _single_count(ctx.neo4j_session, "MATCH (n) RETURN count(n) AS c"),
                "relationships": await _single_count(ctx.neo4j_session, "MATCH ()-[r]->() RETURN count(r) AS c"),
            }
        except Exception:  # progress is cosmetic; never fail the backup over it
            totals = {"nodes": 0, "relationships": 0}
        grand_total = max(1, totals["nodes"] + totals["relationships"])
        with open(destination / "graph.json", "w", encoding="utf-8") as handle:
            handle.write("{")
            for index, (kind, (query, keys)) in enumerate(queries.items()):
                handle.write(("," if index else "") + f'"{kind}": [')
                result = await ctx.neo4j_session.run(query)
                async for record in result:
                    if counts[kind]:
                        handle.write(",")
                    handle.write(json.dumps({key: record[key] for key in keys}, default=service._json_default))
                    counts[kind] += 1
                    if counts[kind] % 5000 == 0:
                        done = counts["nodes"] + counts["relationships"]
                        await ctx.report(
                            f"Graph: {counts[kind]:,} of {totals[kind]:,} {kind} written", min(0.99, done / grand_total)
                        )
                handle.write("]")
            handle.write("}")
        await ctx.report("Graph: writing schema")
        schema = await service._export_neo4j_schema(ctx.neo4j_session)
        with open(destination / "schema.json", "w", encoding="utf-8") as handle:
            json.dump(schema, handle, default=service._json_default)
        records = [
            FileRecord(f"{name}", path.stat().st_size, path.stat().st_mtime_ns, sha256_file(path))
            for name in ("graph.json", "schema.json")
            if (path := destination / name).exists()
        ]
        return ExportResult(records, counts)

    async def restore(self, source: Path, ctx: BackupContext) -> dict[str, Any]:
        from app.services.backup_service import BackupService

        if ctx.neo4j_session is None:
            raise RuntimeError("Neo4j session is required to restore the graph")
        service = BackupService()
        summary = await service._restore_neo4j_dump(ctx.neo4j_session, source / "graph.json", source / "schema.json")
        return {"neo4j": summary}


class QdrantEmbeddingsCategory(BackupCategory):
    id = "embeddings"
    label = "Embeddings / vector store (Qdrant)"
    description = "ShreckWorld semantic-search vectors, captured with Qdrant's native snapshots."
    owner = "shreckworld"
    restart_required = False
    BYTES_PER_DIMENSION = 4

    def __init__(self, settings_factory: Callable[[], Settings] = get_settings) -> None:
        self._settings_factory = settings_factory

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._settings_factory().qdrant_url.rstrip("/"), timeout=httpx.Timeout(600.0, connect=5.0))

    async def _collections(self, client: httpx.AsyncClient) -> list[str]:
        response = await client.get("/collections")
        response.raise_for_status()
        return [item["name"] for item in response.json()["result"]["collections"]]

    async def estimate(self, ctx: BackupContext) -> Estimate:
        try:
            async with self._client() as client:
                total = 0
                collections = await self._collections(client)
                for name in collections:
                    info = (await client.get(f"/collections/{name}")).json()["result"]
                    vectors = info.get("points_count") or 0
                    params = info.get("config", {}).get("params", {}).get("vectors", {})
                    dimension = params.get("size", 0) if isinstance(params, dict) else 0
                    total += vectors * (dimension * self.BYTES_PER_DIMENSION + 512)
                return Estimate(total, len(collections), approximate=True)
        except Exception as exc:
            logger.warning("Could not estimate Qdrant size: %s", exc)
            return Estimate(0, 0, approximate=True)

    async def export(self, destination: Path, ctx: BackupContext) -> ExportResult:
        destination.mkdir(parents=True, exist_ok=True)
        records: list[FileRecord] = []
        async with self._client() as client:
            for name in await self._collections(client):
                created = await client.post(f"/collections/{name}/snapshots", params={"wait": "true"})
                created.raise_for_status()
                snapshot_name = created.json()["result"]["name"]
                target = destination / f"{name}.snapshot"
                try:
                    async with client.stream("GET", f"/collections/{name}/snapshots/{snapshot_name}") as stream:
                        stream.raise_for_status()
                        with open(target, "wb") as handle:
                            async for chunk in stream.aiter_bytes(1024 * 1024):
                                handle.write(chunk)
                finally:
                    await client.delete(f"/collections/{name}/snapshots/{snapshot_name}", params={"wait": "true"})
                stat = target.stat()
                records.append(FileRecord(target.name, stat.st_size, stat.st_mtime_ns, await asyncio.to_thread(sha256_file, target)))
        return ExportResult(records, {"collections": [record.path.removesuffix(".snapshot") for record in records]})

    async def restore(self, source: Path, ctx: BackupContext) -> dict[str, Any]:
        restored: list[str] = []
        async with self._client() as client:
            for snapshot in sorted(source.glob("*.snapshot")):
                name = snapshot.name.removesuffix(".snapshot")
                with open(snapshot, "rb") as handle:
                    response = await client.post(
                        f"/collections/{name}/snapshots/upload",
                        params={"priority": "snapshot", "wait": "true"},
                        files={"snapshot": (snapshot.name, handle, "application/octet-stream")},
                    )
                response.raise_for_status()
                restored.append(name)
        return {"collections": restored}


class ShreckRpgCategory(FileCategory):
    """ShreckRPG files; on restore it can keep only one Shrecknet user's data."""

    async def restore(self, source: Path, ctx: BackupContext) -> dict[str, Any]:
        keep_user = ctx.options.get("rpg_keep_user_id")
        if keep_user is None:
            return await super().restore(source, ctx)

        from app.services.backup.rpg_prune import prune_to_user

        file_source = self._sources()[0]
        # hidden entries are ignored by restore, and the same filesystem keeps the final swap atomic
        staged_root = file_source.root / f".rpg-restore-{os.getpid()}"
        staged = staged_root / file_source.label

        def prepare() -> dict[str, Any]:
            remove_path(staged_root)
            shutil.copytree(source / file_source.label, staged)
            return prune_to_user(staged, keep_user).to_dict()

        try:
            report = await asyncio.to_thread(prepare)
            summary = await super().restore(staged_root, ctx)
        finally:
            remove_path(staged_root)
        return {**summary, "pruned_to_user": report}


class BundleCategory(BackupCategory):
    """A user-facing backup unit made of parts that must be captured and restored together."""

    def __init__(
        self,
        *,
        id: str,
        label: str,
        description: str,
        owner: str,
        parts: list[BackupCategory],
        sensitive: bool = False,
        default_selected: bool = True,
    ) -> None:
        self.id = id
        self.label = label
        self.description = description
        self.owner = owner
        self.sensitive = sensitive
        self.default_selected = default_selected
        self.parts = parts

    @property
    def restart_required(self) -> bool:  # type: ignore[override]
        return any(part.restart_required for part in self.parts)

    def unavailable_reason(self) -> str | None:
        for part in self.parts:
            reason = part.unavailable_reason()
            if reason:
                return reason
        return None

    async def estimate(self, ctx: BackupContext) -> Estimate:
        total = files = 0
        approximate = False
        for part in self.parts:
            estimate = await part.estimate(ctx)
            total += estimate.size_bytes
            files += estimate.file_count
            approximate = approximate or estimate.approximate
        return Estimate(total, files, approximate)

    async def export(self, destination: Path, ctx: BackupContext) -> ExportResult:
        records: list[FileRecord] = []
        details: dict[str, Any] = {}
        previous_root = ctx.previous_root
        previous_all = ctx.previous_records
        outer_note = ctx.note
        try:
            for position, part in enumerate(self.parts):
                await ctx.report(part.label, position / len(self.parts))
                if outer_note is not None:
                    ctx.note = self._windowed(outer_note, position)
                prefix = f"{part.id}/"
                ctx.previous_records = {
                    key[len(prefix) :]: value for key, value in previous_all.items() if key.startswith(prefix)
                }
                ctx.previous_root = previous_root / part.id if previous_root else None
                result = await part.export(destination / part.id, ctx)
                records.extend(replace(record, path=prefix + record.path) for record in result.records)
                details[part.id] = result.details
        finally:
            ctx.previous_records, ctx.previous_root = previous_all, previous_root
            ctx.note = outer_note
        return ExportResult(records, details)

    def _windowed(self, outer: Any, position: int) -> Any:
        async def note(text: str, fraction: float | None = None) -> None:
            await outer(text, (position + (fraction or 0)) / len(self.parts))

        return note

    async def restore(self, source: Path, ctx: BackupContext) -> dict[str, Any]:
        missing = [part.id for part in self.parts if not (source / part.id).is_dir()]
        if missing:
            raise RuntimeError(f"Snapshot is missing parts of {self.label}: {', '.join(missing)}")
        summary: dict[str, Any] = {}
        for position, part in enumerate(self.parts):
            await ctx.report(part.label, position / len(self.parts))
            summary[part.id] = await part.restore(source / part.id, ctx)
        return summary


async def _single_count(session: Any, query: str) -> int:
    result = session.run(query)
    if inspect.isawaitable(result):
        result = await result
    record = await result.single()
    return int(record["c"]) if record else 0


def _data_dir() -> Path:
    return _default_data_dir()


def _media_root() -> Path:
    return Path(get_settings().media_root)


def _optional_dir(value: str) -> Path | None:
    return Path(value) if value else None


def _dir_requirement(value: str, env_name: str) -> Callable[[], str | None]:
    def check() -> str | None:
        path = _optional_dir(getattr(get_settings(), value))
        if path is None:
            return f"Not configured. Set {env_name} and mount the data directory into the Shrecknet containers."
        if not path.is_dir():
            return f"Directory {path} is not available in this container."
        return None

    return check


DOCUMENT_DIRS = ("library", "library_item")
IMAGE_DIRS = ("avatars", "entity", "ontology", "user", "content", "agent")
COMPANION_DIRS = ("chats", "companion_orchestrator", "character_agent")
RESERVED_MEDIA_DIRS = frozenset({"backups", "secrets", "local_tests"})
CLAIMED_MEDIA_DIRS = frozenset(DOCUMENT_DIRS + IMAGE_DIRS + COMPANION_DIRS) | RESERVED_MEDIA_DIRS


def _main_databases(entry: Path) -> bool:
    jobs_name = Path(get_settings().jobs_database_url.rsplit("/", 1)[-1]).name
    excluded = {CONFIG_DB_FILENAME, LEGACY_CONFIG_DB_FILENAME, jobs_name}
    return entry.is_file() and entry.suffix == ".db" and entry.name not in excluded


def build_parts() -> dict[str, BackupCategory]:
    def media(*dirs: str) -> Callable[[], list[FileSource]]:
        return lambda: [FileSource("media", _media_root(), names(*dirs))]

    def external(setting: str, label: str, *, media_setting: str | None = None) -> Callable[[], list[FileSource]]:
        def sources() -> list[FileSource]:
            found = [FileSource(label, Path(getattr(get_settings(), setting) or "/nonexistent"), lambda e: e.name != "backups")]
            if media_setting:
                found.append(
                    FileSource("media", Path(getattr(get_settings(), media_setting) or "/nonexistent"), lambda e: e.name != "backups")
                )
            return found

        return sources

    parts: list[BackupCategory] = [
        FileCategory(
            id="shrecknet_db",
            label="Shrecknet data",
            description="Users, worlds, ontologies, pages, library records and embeddings stored in the relational database.",
            owner="shrecknet",
            sources=lambda: [FileSource("databases", _data_dir(), _main_databases)],
            restart_required=True,
        ),
        Neo4jGraphCategory(),
        FileCategory(
            id="documents",
            label="PDFs and documents",
            description="Uploaded library PDFs and their parsed content.",
            owner="shrecknet",
            sources=media(*DOCUMENT_DIRS),
        ),
        FileCategory(
            id="images",
            label="Images and media",
            description="Avatars, entity and ontology images, content and agent media.",
            owner="shrecknet",
            sources=lambda: [
                FileSource("media", _media_root(), lambda e: e.name in IMAGE_DIRS or (e.name not in CLAIMED_MEDIA_DIRS))
            ],
        ),
        FileCategory(
            id="companion",
            label="Companion and chats",
            description="Chat history media, companion orchestration and character-agent state.",
            owner="shrecknet",
            sources=lambda: [
                FileSource("media", _media_root(), names(*COMPANION_DIRS)),
                FileSource("databases", _data_dir(), names("companion_attachments")),
            ],
        ),
        FileCategory(
            id="shreckworld_files",
            label="ShreckWorld data",
            description="ShreckWorld database, uploaded sources and parsed world files.",
            owner="shreckworld",
            sources=external("backup_shreckworld_data_dir", "databases", media_setting="backup_shreckworld_media_dir"),
            restart_required=True,
            requires=_dir_requirement("backup_shreckworld_data_dir", "SHRECKNET_BACKUP_SHRECKWORLD_DATA_DIR"),
        ),
        QdrantEmbeddingsCategory(),
        ShreckRpgCategory(
            id="shreckrpg_files",
            label="ShreckRPG data",
            description="ShreckRPG database, configuration and chronicle images.",
            owner="shreckrpg",
            sources=external("backup_shreckrpg_data_dir", "datasets"),
            restart_required=True,
            requires=_dir_requirement("backup_shreckrpg_data_dir", "SHRECKNET_BACKUP_SHRECKRPG_DATA_DIR"),
        ),
        FileCategory(
            id="shrecknet_config",
            label="Shrecknet configuration",
            description="Runtime settings stored by the admin configuration pages.",
            owner="shrecknet",
            sources=lambda: [FileSource("databases", _data_dir(), sqlite_files(CONFIG_DB_FILENAME, LEGACY_CONFIG_DB_FILENAME))],
            restart_required=True,
        ),
        FileCategory(
            id="shreckllm_files",
            label="ShreckLLM configuration",
            description="ShreckLLM providers, models and runtime settings.",
            owner="shreckllm",
            sources=external("backup_shreckllm_data_dir", "databases"),
            restart_required=True,
            requires=_dir_requirement("backup_shreckllm_data_dir", "SHRECKNET_BACKUP_SHRECKLLM_DATA_DIR"),
        ),
        FileCategory(
            id="secrets_files",
            label="Secrets and keys",
            description="Runtime secrets and signing keys.",
            owner="shrecknet",
            sources=lambda: [
                FileSource("databases", _data_dir(), names("runtime-secrets")),
                FileSource("media", _media_root(), names("secrets")),
            ],
            sensitive=True,
            default_selected=False,
            restart_required=True,
        ),
    ]
    return {part.id: part for part in parts}


def build_categories() -> list[BackupCategory]:
    """User-facing bundles. Data that references each other is never split across bundles."""
    parts = build_parts()

    def pick(*ids: str) -> list[BackupCategory]:
        return [parts[item] for item in ids]

    return [
        BundleCategory(
            id="shrecknet_worlds",
            label="Shrecknet Worlds",
            description=(
                "Database, knowledge graph, ontologies, embeddings, Companion and character agents, "
                "documents and images. These reference one another and are always restored together."
            ),
            owner="shrecknet",
            parts=pick("shrecknet_db", "graph", "documents", "images", "companion"),
        ),
        BundleCategory(
            id="shreckworlds",
            label="ShreckWorlds",
            description=(
                "ShreckWorld worlds, library items, PDFs and semantic-search embeddings. "
                "Worlds whose ontology is missing stay usable and appear as unlinked."
            ),
            owner="shreckworld",
            parts=pick("shreckworld_files", "embeddings"),
        ),
        BundleCategory(
            id="shreckrpg",
            label="ShreckRPG",
            description=(
                "Games, chronicles, notes, sessions, users' RPG activity and chronicle images. "
                "Games reference Shrecknet users and ontologies, so restore it with Shrecknet Worlds when moving to a new installation."
            ),
            owner="shreckrpg",
            parts=pick("shreckrpg_files"),
        ),
        BundleCategory(
            id="shrecknet_config",
            label="Shrecknet configuration",
            description="Settings stored by the Shrecknet admin configuration pages.",
            owner="shrecknet",
            parts=pick("shrecknet_config"),
        ),
        BundleCategory(
            id="shreckllm_config",
            label="ShreckLLM configuration",
            description="ShreckLLM providers, models and runtime settings.",
            owner="shreckllm",
            parts=pick("shreckllm_files"),
        ),
        BundleCategory(
            id="secrets",
            label="Secrets and keys",
            description="Runtime secrets and signing keys. Sensitive: store backups of this category safely.",
            owner="shrecknet",
            parts=pick("secrets_files"),
            sensitive=True,
            default_selected=False,
        ),
    ]


def category_map() -> dict[str, BackupCategory]:
    return {category.id: category for category in build_categories()}
