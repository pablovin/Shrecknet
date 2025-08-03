"""Add news targets table and remove user_id from news

Revision ID: 20240930_01
Revises: 20240910_01
Create Date: 2024-09-30
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20240930_01"
down_revision = "20240910_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create news target table and drop user_id column."""
    with op.batch_alter_table("news") as batch_op:
        batch_op.drop_column("user_id")
    op.create_table(
        "newstarget",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("news_id", sa.Integer(), sa.ForeignKey("news.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
    )


def downgrade() -> None:
    """Drop news target table and restore user_id column."""
    op.drop_table("newstarget")
    with op.batch_alter_table("news") as batch_op:
        batch_op.add_column(
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True)
        )
