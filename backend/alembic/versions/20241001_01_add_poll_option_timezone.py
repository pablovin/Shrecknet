"""Add timezone to session poll option

Revision ID: 20241001_01
Revises: 20240930_01
Create Date: 2024-10-01
"""

from alembic import op
import sqlalchemy as sa


revision = "20241001_01"
down_revision = "20240930_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessionpolloption",
        sa.Column("timezone", sa.String(), nullable=False, server_default="UTC"),
    )
    op.alter_column("sessionpolloption", "timezone", server_default=None)


def downgrade() -> None:
    op.drop_column("sessionpolloption", "timezone")
