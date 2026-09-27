"""Suites, test cases, site credentials + run/step links for regression flow."""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "site_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("label", sa.String(100), nullable=False, server_default="Доступ"),
        sa.Column("login_url", sa.String(1024), nullable=False, server_default=""),
        sa.Column("username", sa.String(255), nullable=False, server_default=""),
        sa.Column("password_encrypted", sa.Text(), nullable=False),
        sa.Column("username_selector", sa.String(512), nullable=False, server_default=""),
        sa.Column("password_selector", sa.String(512), nullable=False, server_default=""),
        sa.Column("submit_selector", sa.String(512), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "test_suites",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("url", sa.String(1024), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False, server_default="specific"),
        sa.Column("credential_id", sa.String(36),
                  sa.ForeignKey("site_credentials.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "test_cases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("suite_id", sa.String(36), sa.ForeignKey("test_suites.id"), nullable=False, index=True),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("action", sa.String(50), nullable=False, server_default="click"),
        sa.Column("selector", sa.String(512), nullable=False, server_default=""),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("expected", sa.Text(), nullable=False, server_default=""),
        sa.Column("expected_effect", sa.String(40), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source", sa.String(10), nullable=False, server_default="auto"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.add_column("test_runs", sa.Column("suite_id", sa.String(36), nullable=True))
    op.create_index("ix_test_runs_suite_id", "test_runs", ["suite_id"])
    op.add_column("test_runs", sa.Column("credential_id", sa.String(36), nullable=True))
    op.add_column("test_runs", sa.Column("share_token", sa.String(64), nullable=True))
    op.create_index("ix_test_runs_share_token", "test_runs", ["share_token"])
    op.add_column("test_steps", sa.Column("test_case_id", sa.String(36), nullable=True))


def downgrade() -> None:
    op.drop_column("test_steps", "test_case_id")
    op.drop_index("ix_test_runs_share_token", table_name="test_runs")
    op.drop_column("test_runs", "share_token")
    op.drop_column("test_runs", "credential_id")
    op.drop_index("ix_test_runs_suite_id", table_name="test_runs")
    op.drop_column("test_runs", "suite_id")
    op.drop_table("test_cases")
    op.drop_table("test_suites")
    op.drop_table("site_credentials")
