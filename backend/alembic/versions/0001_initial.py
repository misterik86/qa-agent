"""initial schema (full DDL from SQLAlchemy models)

Revision ID: 0001
Revises:
Create Date: 2026-01-01
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------- users ----------
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("plan", sa.Enum("trial", "pro", "enterprise", "enterprise_trial", "blocked", name="plan"),
                  nullable=False, server_default="trial"),
        sa.Column("trial_started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("plan_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("subscription_id", sa.String(36), nullable=True),
        sa.Column("tests_used_this_month", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quota_month", sa.String(7), nullable=False, server_default=""),
        sa.Column("email_consent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("locale", sa.String(5), nullable=False, server_default="ru"),
        sa.Column("telegram_chat_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_id", "users", ["id"])

    # ---------- refresh_tokens ----------
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True)

    # ---------- api_keys ----------
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False, server_default="Default"),
        sa.Column("key_hash", sa.String(128), nullable=False),
        sa.Column("prefix", sa.String(12), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)

    # ---------- user_consents ----------
    op.create_table(
        "user_consents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("accepted_tos", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("accepted_privacy", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_user_consents_user_id", "user_consents", ["user_id"])

    # ---------- audit_logs ----------
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=True),
        sa.Column("event", sa.Enum("login", "logout", "register", "email_verified", "password_change",
                                   "scenario_create", "scenario_update", "scenario_delete",
                                   "scenario_deprecate", "test_run_start", "subscription_change",
                                   name="auditevent"), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False, server_default=""),
        sa.Column("entity_id", sa.String(50), nullable=True),
        sa.Column("ip", sa.String(45), nullable=False, server_default=""),
        sa.Column("user_agent", sa.String(255), nullable=False, server_default=""),
        sa.Column("meta", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])

    # ---------- scenarios ----------
    op.create_table(
        "scenarios",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("url", sa.String(1024), nullable=False),
        sa.Column("mode", sa.Enum("specific", "full_page", name="scenariomode"),
                  nullable=False, server_default="specific"),
        sa.Column("script_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Enum("draft", "active", "deprecated", "failed", name="scenariostatus"),
                  nullable=False, server_default="draft"),
        sa.Column("tags", sa.String(255), nullable=False, server_default=""),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("template_key", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_scenarios_owner_id", "scenarios", ["owner_id"])

    # ---------- scenario_versions ----------
    op.create_table(
        "scenario_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scenario_id", sa.String(36), sa.ForeignKey("scenarios.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.Text(), nullable=False),
        sa.Column("author_id", sa.String(36), nullable=False),
        sa.Column("change_comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_scenario_versions_scenario_id", "scenario_versions", ["scenario_id"])

    # ---------- test_runs ----------
    op.create_table(
        "test_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("scenario_id", sa.String(36), sa.ForeignKey("scenarios.id"), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("url", sa.String(1024), nullable=False),
        sa.Column("mode", sa.Enum("specific", "full_page", name="runmode"),
                  nullable=False, server_default="specific"),
        sa.Column("script_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Enum("queued", "running", "passed", "failed", "cancelled",
                                    "timeout", "limit_exceeded", "error", name="runstatus"),
                  nullable=False, server_default="queued"),
        sa.Column("container_id", sa.String(128), nullable=True),
        sa.Column("celery_task_id", sa.String(128), nullable=True),
        sa.Column("total_steps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed_steps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_steps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("console_errors", sa.Text(), nullable=False, server_default=""),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timeout_minutes", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_test_runs_owner_id", "test_runs", ["owner_id"])
    op.create_index("ix_test_runs_scenario_id", "test_runs", ["scenario_id"])

    # ---------- test_steps ----------
    op.create_table(
        "test_steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("test_run_id", sa.String(36), sa.ForeignKey("test_runs.id"), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("action", sa.String(50), nullable=False, server_default=""),
        sa.Column("selector", sa.String(512), nullable=False, server_default=""),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("expected", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Enum("passed", "failed", "pending", "skipped", "error", name="stepstatus"),
                  nullable=False, server_default="pending"),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("screenshot_path", sa.String(512), nullable=True),
        sa.Column("console_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_test_steps_test_run_id", "test_steps", ["test_run_id"])

    # ---------- test_run_artifacts ----------
    op.create_table(
        "test_run_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("test_run_id", sa.String(36), sa.ForeignKey("test_runs.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("format", sa.String(20), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_test_run_artifacts_test_run_id", "test_run_artifacts", ["test_run_id"])

    # ---------- time_predictions ----------
    op.create_table(
        "time_predictions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("test_run_id", sa.String(36), sa.ForeignKey("test_runs.id"), nullable=False),
        sa.Column("predicted_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actual_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("base_seconds", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("steps_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("elements_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correction_factor", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_time_predictions_test_run_id", "time_predictions", ["test_run_id"])

    # ---------- schedule_jobs ----------
    op.create_table(
        "schedule_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("scenario_id", sa.String(36), sa.ForeignKey("scenarios.id"), nullable=False),
        sa.Column("frequency", sa.Enum("hourly", "daily", "weekly", name="schedulefrequency"), nullable=False),
        sa.Column("channel", sa.Enum("telegram", "vk", "email", name="deliverychannel"), nullable=False),
        sa.Column("chat_id", sa.String(255), nullable=True),
        sa.Column("vk_user_id", sa.String(255), nullable=True),
        sa.Column("vk_token_encrypted", sa.Text(), nullable=True),
        sa.Column("email_recipient", sa.String(255), nullable=True),
        sa.Column("status", sa.Enum("active", "paused", "limit_exceeded", name="schedulestatus"),
                  nullable=False, server_default="active"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_schedule_jobs_owner_id", "schedule_jobs", ["owner_id"])
    op.create_index("ix_schedule_jobs_scenario_id", "schedule_jobs", ["scenario_id"])

    # ---------- schedule_run_logs ----------
    op.create_table(
        "schedule_run_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("schedule_job_id", sa.String(36), sa.ForeignKey("schedule_jobs.id"), nullable=False),
        sa.Column("test_run_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="sent"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_schedule_run_logs_schedule_job_id", "schedule_run_logs", ["schedule_job_id"])

    # ---------- webhook_configs ----------
    op.create_table(
        "webhook_configs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("url", sa.String(1024), nullable=False),
        sa.Column("event", sa.Enum("test_finished", name="webhookevent"),
                  nullable=False, server_default="test_finished"),
        sa.Column("secret", sa.String(255), nullable=False, server_default=""),
        sa.Column("status", sa.Enum("active", "inactive", name="webhookstatus"),
                  nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_webhook_configs_owner_id", "webhook_configs", ["owner_id"])

    # ---------- webhook_deliveries ----------
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("webhook_id", sa.String(36), sa.ForeignKey("webhook_configs.id"), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_webhook_deliveries_webhook_id", "webhook_deliveries", ["webhook_id"])

    # ---------- subscriptions ----------
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("plan", sa.Enum("pro", "enterprise", name="subscriptionplan"), nullable=False),
        sa.Column("status", sa.Enum("active", "cancelled", "expired", "pending", name="subscriptionstatus"),
                  nullable=False, server_default="active"),
        sa.Column("payment_id", sa.String(64), nullable=True),
        sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])

    # ---------- payments ----------
    op.create_table(
        "payments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("yookassa_payment_id", sa.String(64), nullable=False),
        sa.Column("plan", sa.Enum("pro", "enterprise", name="subscriptionplan"), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="RUB"),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_payments_user_id", "payments", ["user_id"])
    op.create_index("ix_payments_yookassa_payment_id", "payments", ["yookassa_payment_id"])


def downgrade() -> None:
    for table in (
        "payments", "subscriptions", "webhook_deliveries", "webhook_configs",
        "schedule_run_logs", "schedule_jobs", "time_predictions", "test_run_artifacts",
        "test_steps", "test_runs", "scenario_versions", "scenarios",
        "audit_logs", "user_consents", "api_keys", "refresh_tokens", "users",
    ):
        op.drop_table(table)

    # PostgreSQL native ENUM types must be dropped explicitly.
    for enum_name in (
        "plan", "auditevent", "scenariomode", "scenariostatus", "runmode", "runstatus",
        "stepstatus", "schedulefrequency", "deliverychannel", "schedulestatus",
        "webhookevent", "webhookstatus", "subscriptionplan", "subscriptionstatus",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
