"""Make session.scheduled_time nullable

Revision ID: 20240905_01
Revises: 20240720_01
Create Date: 2024-09-05
"""

from alembic import op
import sqlalchemy as sa

revision = "20240905_01"
down_revision = "20240720_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("session", schema=None) as batch_op:
        batch_op.alter_column(
            "scheduled_time", existing_type=sa.DateTime(), nullable=True
        )


def downgrade() -> None:
    with op.batch_alter_table("session", schema=None) as batch_op:
        batch_op.alter_column(
            "scheduled_time", existing_type=sa.DateTime(), nullable=False
        )
