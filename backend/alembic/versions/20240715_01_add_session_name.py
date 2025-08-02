"""Add name column to session table

Revision ID: 20240715_01
Revises: 20240607_03
Create Date: 2024-07-15
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20240715_01"
down_revision = "20240607_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the non-nullable name column to the session table."""
    op.add_column(
        "session",
        sa.Column("name", sa.String(), nullable=False, server_default=""),
    )
    # Remove default now that existing rows have a value
    op.alter_column("session", "name", server_default=None)


def downgrade() -> None:
    """Remove the name column from the session table."""
    op.drop_column("session", "name")
