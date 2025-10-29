"""
Import endpoints to migrate data from old backend to new backend_2.

This module provides endpoints to import:
- Users (username, password, full_name, timezone, role)
- Game tables (convert Table -> Game with members)
- Sessions (only with scheduled_date, not polls, include attendees)
"""

import logging
from typing import Any
import os

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select, text, insert

from app.api.deps import get_current_admin_user
from app.db.session import get_session
from app.models.user import User, UserRole
from app.models.game import Game, GameSession, GameSessionAttendance, game_members
from app.models.ontology import Ontology

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/imports", tags=["imports"])


# Old backend database connection
# Default path points to backend/data/prod.db (relative to backend_2 directory)
# Can be overridden with OLD_DATABASE_URL environment variable
OLD_DATABASE_URL = os.getenv(
    "OLD_DATABASE_URL",
    "sqlite+aiosqlite:///../backend/data/prod.db",
)


async def get_old_db_session():
    """Create a session for the old database."""
    engine = create_async_engine(OLD_DATABASE_URL, echo=False)
    async_session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session_maker() as session:
        return session


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def import_users(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_admin_user),
) -> dict[str, Any]:
    """
    Import users from old backend to new backend_2.

    Imports: username (nickname), password (hashed), full_name, timezone, role.
    Images are not imported.
    """
    logger.info("Starting user import process")

    imported_count = 0
    skipped_count = 0
    error_count = 0

    try:
        # Connect to old database
        old_engine = create_async_engine(OLD_DATABASE_URL, echo=False)
        old_session_maker = async_sessionmaker(old_engine, expire_on_commit=False)

        async with old_session_maker() as old_session:
            # Fetch all users from old database using raw SQL
            result = await old_session.execute(
                text(
                    "SELECT id, nickname, email, hashed_password, role, timezone FROM user"
                )
            )
            old_users = result.fetchall()
            logger.info(f"Found {len(old_users)} users in old database")

            for old_user in old_users:
                try:
                    # Check if user already exists in new database
                    check_result = await session.execute(
                        select(User).where(User.email == old_user.email)
                    )
                    existing_user = check_result.scalar_one_or_none()

                    if existing_user:
                        logger.info(f"User {old_user.email} already exists, skipping")
                        skipped_count += 1
                        continue

                    # Map old role to new role
                    role_mapping = {
                        "system admin": UserRole.ADMIN,
                        "world builder": UserRole.WORLD_BUILDER,
                        "writer": UserRole.WRITER,
                        "player": UserRole.PLAYER,
                    }
                    new_role = role_mapping.get(old_user.role, UserRole.PLAYER)

                    # Create new user
                    new_user = User(
                        username=old_user.nickname,
                        email=old_user.email,
                        hashed_password=old_user.hashed_password,
                        full_name=old_user.nickname,  # Using nickname as full_name since old model doesn't have it
                        timezone=old_user.timezone or "UTC",
                        role=new_role,
                    )

                    session.add(new_user)
                    imported_count += 1
                    logger.info(f"Imported user: {old_user.email}")

                except Exception as e:
                    error_count += 1
                    logger.error(f"Error importing user {old_user.email}: {str(e)}")

            await session.commit()
            logger.info(
                f"User import completed: {imported_count} imported, {skipped_count} skipped, {error_count} errors"
            )

        await old_engine.dispose()

    except Exception as e:
        logger.error(f"Fatal error during user import: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to import users: {str(e)}",
        )

    return {
        "message": "User import completed",
        "imported": imported_count,
        "skipped": skipped_count,
        "errors": error_count,
    }


@router.post("/game-tables", status_code=status.HTTP_201_CREATED)
async def import_game_tables(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_admin_user),
) -> dict[str, Any]:
    """
    Import game tables from old backend to new backend_2.

    Converts Table -> Game with ontology and members.
    """
    logger.info("Starting game table import process")

    imported_count = 0
    skipped_count = 0
    error_count = 0

    try:
        # First, ensure we have a default ontology
        result = await session.execute(select(Ontology))
        ontologies = result.scalars().all()

        if len(ontologies) == 0:
            # Create a default ontology for imported games
            default_ontology = Ontology(
                name="Imported Games",
                description="Default ontology for games imported from old backend",
            )
            session.add(default_ontology)
            await session.flush()
            default_ontology_id = default_ontology.id
            logger.info(f"Created default ontology with ID: {default_ontology_id}")
        else:
            # Use the first available ontology
            default_ontology_id = ontologies[0].id
            logger.info(f"Using existing ontology with ID: {default_ontology_id}")

        # Connect to old database
        old_engine = create_async_engine(OLD_DATABASE_URL, echo=False)
        old_session_maker = async_sessionmaker(old_engine, expire_on_commit=False)

        async with old_session_maker() as old_session:
            # Fetch all tables from old database
            result = await old_session.execute(
                text(
                    "SELECT id, name, world_id, crest_url, created_by, created_at FROM 'table'"
                )
            )
            old_tables = result.fetchall()
            logger.info(f"Found {len(old_tables)} tables in old database")

            for old_table in old_tables:
                try:
                    # Check if game already exists in new database
                    check_result = await session.execute(
                        select(Game).where(Game.name == old_table.name)
                    )
                    existing_game = check_result.scalar_one_or_none()

                    if existing_game:
                        logger.info(f"Game '{old_table.name}' already exists, skipping")
                        skipped_count += 1
                        continue

                    # Create new game
                    new_game = Game(
                        name=old_table.name,
                        ontology_id=default_ontology_id,
                    )
                    session.add(new_game)
                    await session.flush()  # Get the game ID

                    # Fetch table members from old database
                    members_result = await old_session.execute(
                        text(
                            "SELECT user_id, is_gm FROM tablemember WHERE table_id = :table_id"
                        ),
                        {"table_id": old_table.id},
                    )
                    old_members = members_result.fetchall()

                    # Add members to the new game
                    for old_member in old_members:
                        # Find corresponding user in new database
                        user_result = await session.execute(
                            select(User).where(User.id == old_member.user_id)
                        )
                        new_user = user_result.scalar_one_or_none()

                        if new_user:
                            # Add user to game members
                            await session.execute(
                                insert(game_members).values(
                                    game_id=new_game.id, user_id=new_user.id
                                )
                            )
                            logger.info(
                                f"Added member {new_user.username} to game '{old_table.name}'"
                            )

                    imported_count += 1
                    logger.info(f"Imported game table: {old_table.name}")

                except Exception as e:
                    error_count += 1
                    logger.error(f"Error importing table {old_table.name}: {str(e)}")

            await session.commit()
            logger.info(
                f"Game table import completed: {imported_count} imported, {skipped_count} skipped, {error_count} errors"
            )

        await old_engine.dispose()

    except Exception as e:
        logger.error(f"Fatal error during game table import: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to import game tables: {str(e)}",
        )

    return {
        "message": "Game table import completed",
        "imported": imported_count,
        "skipped": skipped_count,
        "errors": error_count,
    }


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def import_sessions(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_admin_user),
) -> dict[str, Any]:
    """
    Import sessions from old backend to new backend_2.

    Only imports sessions with scheduled_date (final schedule).
    Does not import polls.
    Includes all attendees.
    """
    logger.info("Starting session import process")

    imported_count = 0
    skipped_count = 0
    error_count = 0

    try:
        # Connect to old database
        old_engine = create_async_engine(OLD_DATABASE_URL, echo=False)
        old_session_maker = async_sessionmaker(old_engine, expire_on_commit=False)

        async with old_session_maker() as old_session:
            # Fetch only sessions with scheduled_time from old database
            result = await old_session.execute(
                text(
                    """
                    SELECT s.id, s.table_id, s.name, s.scheduled_time, s.summary, 
                           s.location, s.timezone, s.created_by, s.created_at,
                           t.name as table_name
                    FROM session s
                    JOIN 'table' t ON s.table_id = t.id
                    WHERE s.scheduled_time IS NOT NULL
                """
                )
            )
            old_sessions = result.fetchall()
            logger.info(
                f"Found {len(old_sessions)} sessions with scheduled dates in old database"
            )

            for old_session_obj in old_sessions:
                try:
                    # Find the game in new database
                    game_result = await session.execute(
                        select(Game).where(Game.name == old_session_obj.table_name)
                    )
                    new_game = game_result.scalar_one_or_none()

                    if not new_game:
                        logger.warning(
                            f"Game '{old_session_obj.table_name}' not found in new database, skipping session"
                        )
                        skipped_count += 1
                        continue

                    # Check if session already exists
                    check_result = await session.execute(
                        select(GameSession).where(
                            GameSession.game_id == new_game.id,
                            GameSession.title == old_session_obj.name,
                        )
                    )
                    existing_session = check_result.scalar_one_or_none()

                    if existing_session:
                        logger.info(
                            f"Session '{old_session_obj.name}' already exists, skipping"
                        )
                        skipped_count += 1
                        continue

                    # Create new game session
                    new_session = GameSession(
                        game_id=new_game.id,
                        title=old_session_obj.name,
                        scheduled_date=old_session_obj.scheduled_time,
                        location=old_session_obj.location,
                        summary=old_session_obj.summary,
                    )
                    session.add(new_session)
                    await session.flush()  # Get the session ID

                    # Fetch session attendances from old database
                    attendance_result = await old_session.execute(
                        text(
                            "SELECT user_id, attending FROM sessionattendance WHERE session_id = :session_id"
                        ),
                        {"session_id": old_session_obj.id},
                    )
                    old_attendances = attendance_result.fetchall()

                    # Add attendances to the new session
                    for old_attendance in old_attendances:
                        # Find corresponding user in new database
                        user_result = await session.execute(
                            select(User).where(User.id == old_attendance.user_id)
                        )
                        new_user = user_result.scalar_one_or_none()

                        if new_user:
                            new_attendance = GameSessionAttendance(
                                session_id=new_session.id,
                                user_id=new_user.id,
                                attending=bool(old_attendance.attending),
                            )
                            session.add(new_attendance)
                            logger.info(
                                f"Added attendance for user {new_user.username} to session '{old_session_obj.name}'"
                            )

                    imported_count += 1
                    logger.info(f"Imported session: {old_session_obj.name}")

                except Exception as e:
                    error_count += 1
                    logger.error(
                        f"Error importing session {old_session_obj.name}: {str(e)}"
                    )

            await session.commit()
            logger.info(
                f"Session import completed: {imported_count} imported, {skipped_count} skipped, {error_count} errors"
            )

        await old_engine.dispose()

    except Exception as e:
        logger.error(f"Fatal error during session import: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to import sessions: {str(e)}",
        )

    return {
        "message": "Session import completed",
        "imported": imported_count,
        "skipped": skipped_count,
        "errors": error_count,
    }
