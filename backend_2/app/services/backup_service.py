"""
Backup and restore service for backend_2.

This service handles:
- Exporting/importing all SQLAlchemy tables to/from JSON
- Exporting/importing Neo4j graph data via Cypher
- Copying/restoring media files
- Creating/extracting tar.gz archives
"""

from __future__ import annotations

import json
import logging
import shutil
import tarfile
from datetime import datetime
from pathlib import Path
from typing import Any

from neo4j import AsyncSession as Neo4jSession
from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.base import Base
from app.models import (
    Agent,
    ArchitectAnalysisRun,
    ArchitectProposal,
    AuditLog,
    ElderChat,
    ElderChatHistory,
    Game,
    GameSession,
    GameSessionAttendance,
    GameSessionPoll,
    GameSessionPollOption,
    GameSessionPollVote,
    LibraryBookmark,
    LibraryItem,
    Note,
    Notification,
    Ontology,
    OntologyEntity,
    OntologyProperty,
    OntologyRelationship,
    User,
)
from app.models.background_job import BackgroundJob

logger = logging.getLogger(__name__)


class BackupService:
    """Service for creating and restoring backups."""

    def __init__(self):
        self.settings = get_settings()
        self.media_root = Path(self.settings.media_root)
        self.backup_dir = self.media_root / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    async def create_backup(
        self,
        db_session: AsyncSession,
        neo4j_session: Neo4jSession,
    ) -> dict[str, Any]:
        """
        Create a complete backup of all data.

        Returns:
            dict with backup metadata including filename and path
        """
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_name = f"backup_{timestamp}"
        backup_temp_dir = Path("/tmp") / backup_name
        backup_temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            logger.info(f"Creating backup: {backup_name}")

            # Export SQLAlchemy database
            logger.info("Exporting database...")
            db_data = await self._export_database(db_session)
            db_file = backup_temp_dir / "database.json"
            with open(db_file, "w") as f:
                json.dump(db_data, f, indent=2, default=str)

            # Export Neo4j graph
            logger.info("Exporting Neo4j graph...")
            neo4j_data = await self._export_neo4j(neo4j_session)
            neo4j_file = backup_temp_dir / "neo4j.json"
            with open(neo4j_file, "w") as f:
                json.dump(neo4j_data, f, indent=2, default=str)

            # Copy media files (excluding backups folder itself)
            logger.info("Copying media files...")
            media_backup_dir = backup_temp_dir / "media"
            if self.media_root.exists():
                shutil.copytree(
                    self.media_root,
                    media_backup_dir,
                    ignore=shutil.ignore_patterns("backups"),
                )

            # Create metadata
            metadata = {
                "created_at": timestamp,
                "database_records": sum(len(v) for v in db_data.values()),
                "neo4j_nodes": len(neo4j_data.get("nodes", [])),
                "neo4j_relationships": len(neo4j_data.get("relationships", [])),
            }
            metadata_file = backup_temp_dir / "metadata.json"
            with open(metadata_file, "w") as f:
                json.dump(metadata, f, indent=2)

            # Create tar.gz archive
            logger.info("Creating archive...")
            backup_archive = self.backup_dir / f"{backup_name}.tar.gz"
            with tarfile.open(backup_archive, "w:gz") as tar:
                tar.add(backup_temp_dir, arcname=backup_name)

            logger.info(f"Backup created successfully: {backup_archive}")

            return {
                "filename": backup_archive.name,
                "path": str(backup_archive),
                "size_bytes": backup_archive.stat().st_size,
                "created_at": timestamp,
                **metadata,
            }

        finally:
            # Clean up temporary directory
            if backup_temp_dir.exists():
                shutil.rmtree(backup_temp_dir)

    async def _export_database(self, session: AsyncSession) -> dict[str, list[dict]]:
        """Export all database tables to JSON-serializable format."""
        data = {}

        # Define table order for import (respecting foreign keys)
        # Users must come first, then ontologies, games, etc.
        table_order = [
            ("users", User),
            ("ontologies", Ontology),
            ("ontology_entities", OntologyEntity),
            ("ontology_properties", OntologyProperty),
            ("ontology_relationships", OntologyRelationship),
            ("agents", Agent),
            ("games", Game),
            ("game_sessions", GameSession),
            ("game_session_polls", GameSessionPoll),
            ("game_session_poll_options", GameSessionPollOption),
            ("game_session_poll_votes", GameSessionPollVote),
            ("game_session_attendance", GameSessionAttendance),
            ("library_items", LibraryItem),
            ("library_bookmarks", LibraryBookmark),
            ("notes", Note),
            ("notifications", Notification),
            ("audit_logs", AuditLog),
            ("elder_chats", ElderChat),
            ("elder_chat_history", ElderChatHistory),
            ("background_jobs", BackgroundJob),
            ("architect_analysis_runs", ArchitectAnalysisRun),
            ("architect_proposals", ArchitectProposal),
        ]

        for table_name, model_class in table_order:
            result = await session.execute(select(model_class))
            instances = result.scalars().all()

            table_data = []
            for instance in instances:
                # Convert SQLAlchemy model to dict
                inspector = inspect(instance)
                item_dict = {}
                for column in inspector.mapper.column_attrs:
                    value = getattr(instance, column.key)
                    # Handle datetime objects
                    if hasattr(value, "isoformat"):
                        value = value.isoformat()
                    item_dict[column.key] = value
                table_data.append(item_dict)

            data[table_name] = table_data
            logger.info(f"Exported {len(table_data)} records from {table_name}")

        # Export many-to-many relationship tables
        # game_members
        result = await session.execute(
            text("SELECT game_id, user_id FROM game_members")
        )
        data["game_members"] = [
            {"game_id": row[0], "user_id": row[1]} for row in result.fetchall()
        ]

        # agent_ontologies
        result = await session.execute(
            text("SELECT agent_id, ontology_id FROM agent_ontologies")
        )
        data["agent_ontologies"] = [
            {"agent_id": row[0], "ontology_id": row[1]} for row in result.fetchall()
        ]

        # note_shares
        result = await session.execute(text("SELECT note_id, user_id FROM note_shares"))
        data["note_shares"] = [
            {"note_id": row[0], "user_id": row[1]} for row in result.fetchall()
        ]

        # library_bookmark_shares
        result = await session.execute(
            text("SELECT bookmark_id, user_id FROM library_bookmark_shares")
        )
        data["library_bookmark_shares"] = [
            {"bookmark_id": row[0], "user_id": row[1]} for row in result.fetchall()
        ]

        return data

    async def _export_neo4j(self, session: Neo4jSession) -> dict[str, list[dict]]:
        """Export Neo4j graph data."""
        data = {"nodes": [], "relationships": []}

        # Export all nodes
        result = await session.run(
            """
            MATCH (n)
            RETURN 
                id(n) as id,
                labels(n) as labels,
                properties(n) as properties
            """
        )
        nodes = await result.data()
        data["nodes"] = nodes
        logger.info(f"Exported {len(nodes)} Neo4j nodes")

        # Export all relationships
        result = await session.run(
            """
            MATCH (a)-[r]->(b)
            RETURN 
                id(r) as id,
                id(a) as start_node_id,
                id(b) as end_node_id,
                type(r) as type,
                properties(r) as properties
            """
        )
        relationships = await result.data()
        data["relationships"] = relationships
        logger.info(f"Exported {len(relationships)} Neo4j relationships")

        return data

    async def restore_backup(
        self,
        backup_path: Path,
        db_session: AsyncSession,
        neo4j_session: Neo4jSession,
        admin_user_id: int | None = None,
    ) -> dict[str, Any]:
        """
        Restore a backup from a tar.gz archive.

        This will:
        1. Clear all existing data
        2. Restore database records
        3. Restore Neo4j graph
        4. Restore media files
        5. Preserve the admin user who invoked the restore

        Args:
            backup_path: Path to the backup tar.gz file
            db_session: Database session
            neo4j_session: Neo4j session
            admin_user_id: ID of the admin user who invoked the restore (to preserve)

        Returns:
            dict with restoration metadata
        """
        if not backup_path.exists():
            raise FileNotFoundError(f"Backup file not found: {backup_path}")

        temp_extract_dir = (
            Path("/tmp") / f"restore_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        )
        temp_extract_dir.mkdir(parents=True, exist_ok=True)

        try:
            logger.info(f"Restoring backup from: {backup_path}")

            # Extract archive
            logger.info("Extracting backup archive...")
            with tarfile.open(backup_path, "r:gz") as tar:
                tar.extractall(temp_extract_dir)

            # Find the backup directory (should be single directory in temp)
            backup_dirs = [d for d in temp_extract_dir.iterdir() if d.is_dir()]
            if not backup_dirs:
                raise ValueError("Invalid backup archive: no data directory found")
            backup_data_dir = backup_dirs[0]

            # Load metadata
            metadata_file = backup_data_dir / "metadata.json"
            if metadata_file.exists():
                with open(metadata_file, "r") as f:
                    metadata = json.load(f)
            else:
                metadata = {}

            # Clear existing data
            logger.info("Clearing existing data...")
            await self._clear_all_data(db_session, neo4j_session)

            # Restore database
            logger.info("Restoring database...")
            db_file = backup_data_dir / "database.json"
            with open(db_file, "r") as f:
                db_data = json.load(f)
            await self._restore_database(db_session, db_data, admin_user_id)

            # Restore Neo4j
            logger.info("Restoring Neo4j graph...")
            neo4j_file = backup_data_dir / "neo4j.json"
            with open(neo4j_file, "r") as f:
                neo4j_data = json.load(f)
            await self._restore_neo4j(neo4j_session, neo4j_data)

            # Restore media files
            logger.info("Restoring media files...")
            media_backup_dir = backup_data_dir / "media"
            if media_backup_dir.exists():
                # Clear existing media (except backups)
                for item in self.media_root.iterdir():
                    if item.name != "backups":
                        if item.is_dir():
                            shutil.rmtree(item)
                        else:
                            item.unlink()

                # Copy restored media
                for item in media_backup_dir.iterdir():
                    dest = self.media_root / item.name
                    if item.is_dir():
                        shutil.copytree(item, dest)
                    else:
                        shutil.copy2(item, dest)

            logger.info("Restore completed successfully")

            return {
                "status": "success",
                "restored_at": datetime.utcnow().isoformat(),
                "backup_metadata": metadata,
            }

        finally:
            # Clean up temporary directory
            if temp_extract_dir.exists():
                shutil.rmtree(temp_extract_dir)

    async def _clear_all_data(
        self, db_session: AsyncSession, neo4j_session: Neo4jSession
    ) -> None:
        """Clear all data from database and Neo4j."""
        # Clear Neo4j first
        logger.info("Clearing Neo4j graph...")
        await neo4j_session.run("MATCH (n) DETACH DELETE n")

        # Clear database tables in reverse dependency order
        logger.info("Clearing database tables...")

        def _get_existing_tables(sync_session) -> set[str]:
            inspector = inspect(sync_session.bind)
            return set(inspector.get_table_names())

        existing_tables = await db_session.run_sync(_get_existing_tables)

        # Delete in reverse order to respect foreign keys
        table_order = [
            "library_bookmark_shares",
            "note_shares",
            "agent_ontologies",
            "game_members",
            "architect_proposals",
            "architect_analysis_runs",
            "background_jobs",
            "elder_chat_history",
            "elder_chats",
            "audit_logs",
            "notifications",
            "notes",
            "library_bookmarks",
            "library_items",
            "game_session_poll_votes",
            "game_session_poll_options",
            "game_session_polls",
            "game_session_attendance",
            "game_sessions",
            "games",
            "agents",
            "ontology_relationships",
            "ontology_properties",
            "ontology_entities",
            "ontologies",
            "users",
        ]

        for table in table_order:
            if table not in existing_tables:
                logger.info("Skipping delete for missing table %s", table)
                continue
            await db_session.execute(text(f'DELETE FROM "{table}"'))

        await db_session.commit()

    async def _restore_database(
        self,
        session: AsyncSession,
        data: dict[str, list[dict]],
        admin_user_id: int | None = None,
    ) -> None:
        """
        Restore database from JSON data.

        Args:
            session: Database session
            data: Backup data dictionary
            admin_user_id: ID of the admin user who invoked the restore (to preserve)
        """
        # Get the admin user data before restore (if provided)
        admin_user_data = None
        if admin_user_id:
            result = await session.execute(select(User).where(User.id == admin_user_id))
            admin_user = result.scalar_one_or_none()
            if admin_user:
                # Store admin user data
                inspector = inspect(admin_user)
                admin_user_data = {}
                for column in inspector.mapper.column_attrs:
                    admin_user_data[column.key] = getattr(admin_user, column.key)
                logger.info(
                    f"Preserving admin user: {admin_user.username} (ID: {admin_user_id})"
                )

        # Restore in the same order as export
        table_order = [
            ("users", User),
            ("ontologies", Ontology),
            ("ontology_entities", OntologyEntity),
            ("ontology_properties", OntologyProperty),
            ("ontology_relationships", OntologyRelationship),
            ("agents", Agent),
            ("games", Game),
            ("game_sessions", GameSession),
            ("game_session_polls", GameSessionPoll),
            ("game_session_poll_options", GameSessionPollOption),
            ("game_session_poll_votes", GameSessionPollVote),
            ("game_session_attendance", GameSessionAttendance),
            ("library_items", LibraryItem),
            ("library_bookmarks", LibraryBookmark),
            ("notes", Note),
            ("notifications", Notification),
            ("audit_logs", AuditLog),
            ("elder_chats", ElderChat),
            ("elder_chat_history", ElderChatHistory),
            ("background_jobs", BackgroundJob),
            ("architect_analysis_runs", ArchitectAnalysisRun),
            ("architect_proposals", ArchitectProposal),
        ]

        for table_name, model_class in table_order:
            if table_name in data:
                records = data[table_name]
                for record in records:
                    # Special handling for users table
                    if table_name == "users" and admin_user_data:
                        # Check if this user conflicts with the admin user
                        if (
                            record.get("username") == admin_user_data["username"]
                            or record.get("email") == admin_user_data["email"]
                        ):
                            # Skip this user from backup, we'll keep the admin user
                            logger.info(
                                f"Skipping backup user {record.get('username')} - conflicts with admin user"
                            )
                            continue

                    instance = model_class(**record)
                    session.add(instance)
                logger.info(f"Restored {len(records)} records to {table_name}")

        # Restore many-to-many tables
        if "game_members" in data:
            for record in data["game_members"]:
                await session.execute(
                    text(
                        "INSERT INTO game_members (game_id, user_id) VALUES (:game_id, :user_id)"
                    ),
                    record,
                )

        if "agent_ontologies" in data:
            for record in data["agent_ontologies"]:
                await session.execute(
                    text(
                        "INSERT INTO agent_ontologies (agent_id, ontology_id) VALUES (:agent_id, :ontology_id)"
                    ),
                    record,
                )

        if "note_shares" in data:
            for record in data["note_shares"]:
                await session.execute(
                    text(
                        "INSERT INTO note_shares (note_id, user_id) VALUES (:note_id, :user_id)"
                    ),
                    record,
                )

        if "library_bookmark_shares" in data:
            for record in data["library_bookmark_shares"]:
                await session.execute(
                    text(
                        "INSERT INTO library_bookmark_shares (bookmark_id, user_id) VALUES (:bookmark_id, :user_id)"
                    ),
                    record,
                )

        await session.commit()

    async def _restore_neo4j(
        self, session: Neo4jSession, data: dict[str, list[dict]]
    ) -> None:
        """Restore Neo4j graph from JSON data."""
        # Create a mapping from old node IDs to new node IDs
        id_mapping = {}

        # Restore nodes
        for node in data.get("nodes", []):
            old_id = node["id"]
            labels = node["labels"]
            properties = node["properties"]

            # Validate labels to prevent Cypher injection
            # Labels should only contain alphanumeric characters and underscores
            validated_labels = []
            for label in labels:
                if not isinstance(label, str) or not all(
                    c.isalnum() or c == "_" for c in label
                ):
                    logger.warning(
                        f"Skipping invalid label: {label}. Labels must be alphanumeric with underscores only."
                    )
                    continue
                validated_labels.append(label)

            if not validated_labels:
                logger.warning(f"Skipping node with no valid labels")
                continue

            labels_str = ":".join(validated_labels)

            # Create node with properties
            query = (
                f"CREATE (n:{labels_str}) SET n = $properties RETURN id(n) as new_id"
            )
            result = await session.run(query, properties=properties)
            record = await result.single()
            new_id = record["new_id"]
            id_mapping[old_id] = new_id

        logger.info(f"Restored {len(id_mapping)} Neo4j nodes")

        # Restore relationships
        for rel in data.get("relationships", []):
            start_id = id_mapping.get(rel["start_node_id"])
            end_id = id_mapping.get(rel["end_node_id"])
            if start_id is None or end_id is None:
                logger.warning(f"Skipping relationship with missing node references")
                continue

            rel_type = rel["type"]
            properties = rel["properties"]

            # Validate relationship type to prevent Cypher injection
            # Relationship types should only contain alphanumeric characters and underscores
            if not isinstance(rel_type, str) or not all(
                c.isalnum() or c == "_" for c in rel_type
            ):
                logger.warning(
                    f"Skipping invalid relationship type: {rel_type}. Types must be alphanumeric with underscores only."
                )
                continue

            query = f"""
            MATCH (a), (b)
            WHERE id(a) = $start_id AND id(b) = $end_id
            CREATE (a)-[r:{rel_type}]->(b)
            SET r = $properties
            """
            await session.run(
                query, start_id=start_id, end_id=end_id, properties=properties
            )

        logger.info(
            f"Restored {len(data.get('relationships', []))} Neo4j relationships"
        )

    def list_backups(self) -> list[dict[str, Any]]:
        """List all available backups."""
        backups = []

        if not self.backup_dir.exists():
            return backups

        for backup_file in sorted(self.backup_dir.glob("backup_*.tar.gz")):
            backups.append(
                {
                    "filename": backup_file.name,
                    "path": str(backup_file),
                    "size_bytes": backup_file.stat().st_size,
                    "created_at": datetime.fromtimestamp(
                        backup_file.stat().st_mtime
                    ).isoformat(),
                }
            )

        return backups

    def get_backup_path(self, filename: str) -> Path:
        """Get the full path for a backup file."""
        backup_path = self.backup_dir / filename
        if not backup_path.exists():
            raise FileNotFoundError(f"Backup not found: {filename}")
        return backup_path
