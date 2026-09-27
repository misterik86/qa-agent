"""Admin panel support: users.is_admin flag + admin_action audit event."""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()))
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE auditevent ADD VALUE IF NOT EXISTS 'admin_action'")


def downgrade() -> None:
    op.drop_column("users", "is_admin")
