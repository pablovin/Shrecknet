"""Add user_id column to news table

Revision ID: 20240910_01
Revises: 20240905_01
Create Date: 2024-09-10
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20240910_01"
down_revision = "20240905_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the optional user_id column to the news table."""
    op.add_column(
        "news",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
    )


def downgrade() -> None:
    """Remove the user_id column from the news table."""
    op.drop_column("news", "user_id")
