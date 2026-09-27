"""Site crawl mode: runmode 'site' enum value."""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE runmode ADD VALUE IF NOT EXISTS 'site'")


def downgrade() -> None:
    pass
