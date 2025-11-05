"""Enforce timezone-aware datetimes for sessions, polls, and tables

Revision ID: 20251105_01
Revises: 20241001_01
Create Date: 2025-11-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# revision identifiers, used by Alembic.
revision = "20251105_01"
down_revision = "20241001_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Update all datetime fields to be timezone-aware.
    Convert naive datetimes to Brussels timezone (Europe/Brussels).
    
    Note: SQLAlchemy/SQLModel stores datetimes as ISO 8601 strings in SQLite.
    We need to ensure all stored datetimes include timezone information.
    """
    conn = op.get_bind()
    brussels_tz = ZoneInfo("Europe/Brussels")
    
    # Get all sessions with scheduled_time
    result = conn.execute(text("SELECT id, scheduled_time FROM session WHERE scheduled_time IS NOT NULL"))
    sessions = result.fetchall()
    
    for session_id, scheduled_time_str in sessions:
        if scheduled_time_str:
            # Parse the datetime
            try:
                dt = datetime.fromisoformat(scheduled_time_str)
                # If it doesn't have timezone info, assume Brussels timezone
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=brussels_tz)
                # Convert to ISO format with timezone
                updated_str = dt.isoformat()
                conn.execute(
                    text("UPDATE session SET scheduled_time = :dt WHERE id = :id"),
                    {"dt": updated_str, "id": session_id}
                )
            except Exception:
                # If parsing fails, skip this record
                pass
    
    # Get all session poll options with proposed_time
    result = conn.execute(text("SELECT id, proposed_time FROM sessionpolloption WHERE proposed_time IS NOT NULL"))
    options = result.fetchall()
    
    for option_id, proposed_time_str in options:
        if proposed_time_str:
            try:
                dt = datetime.fromisoformat(proposed_time_str)
                # If it doesn't have timezone info, assume Brussels timezone
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=brussels_tz)
                updated_str = dt.isoformat()
                conn.execute(
                    text("UPDATE sessionpolloption SET proposed_time = :dt WHERE id = :id"),
                    {"dt": updated_str, "id": option_id}
                )
            except Exception:
                pass
    
    # Ensure created_at fields have timezone info (should already have UTC)
    # For session.created_at
    result = conn.execute(text("SELECT id, created_at FROM session WHERE created_at IS NOT NULL"))
    sessions = result.fetchall()
    
    for session_id, created_at_str in sessions:
        if created_at_str:
            try:
                dt = datetime.fromisoformat(created_at_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                    updated_str = dt.isoformat()
                    conn.execute(
                        text("UPDATE session SET created_at = :dt WHERE id = :id"),
                        {"dt": updated_str, "id": session_id}
                    )
            except Exception:
                pass
    
    # For sessionpoll.created_at
    result = conn.execute(text("SELECT id, created_at FROM sessionpoll WHERE created_at IS NOT NULL"))
    polls = result.fetchall()
    
    for poll_id, created_at_str in polls:
        if created_at_str:
            try:
                dt = datetime.fromisoformat(created_at_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                    updated_str = dt.isoformat()
                    conn.execute(
                        text("UPDATE sessionpoll SET created_at = :dt WHERE id = :id"),
                        {"dt": updated_str, "id": poll_id}
                    )
            except Exception:
                pass
    
    # For table.created_at
    result = conn.execute(text('SELECT id, created_at FROM "table" WHERE created_at IS NOT NULL'))
    tables = result.fetchall()
    
    for table_id, created_at_str in tables:
        if created_at_str:
            try:
                dt = datetime.fromisoformat(created_at_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                    updated_str = dt.isoformat()
                    conn.execute(
                        text('UPDATE "table" SET created_at = :dt WHERE id = :id'),
                        {"dt": updated_str, "id": table_id}
                    )
            except Exception:
                pass


def downgrade() -> None:
    """
    Downgrade is not necessary as this migration only ensures data quality.
    Datetimes will remain in their timezone-aware format.
    """
    pass
