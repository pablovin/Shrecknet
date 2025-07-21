from alembic import op
import sqlalchemy as sa

revision = '20240607_03'
down_revision = '20240607_02'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('usernote', sa.Column('contributors', sa.JSON(), nullable=True))
    op.add_column('usernote', sa.Column('locked_by_user_id', sa.Integer(), nullable=True))
    op.add_column('usernote', sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('usernote', 'contributors')
    op.drop_column('usernote', 'locked_by_user_id')
    op.drop_column('usernote', 'locked_at')
