"""Add timezone columns to user and session

Revision ID: 20240720_01
Revises: 20240715_01
Create Date: 2024-07-20
"""

from alembic import op
import sqlalchemy as sa

revision = "20240720_01"
down_revision = "20240715_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user", sa.Column("timezone", sa.String(), nullable=True))
    op.add_column(
        "session",
        sa.Column("timezone", sa.String(), nullable=False, server_default="UTC"),
    )
    op.alter_column("session", "timezone", server_default=None)


def downgrade() -> None:
    op.drop_column("session", "timezone")
    op.drop_column("user", "timezone")
