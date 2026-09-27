import enum
import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, Enum, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuditEvent(str, enum.Enum):
    login = "login"
    logout = "logout"
    register = "register"
    email_verified = "email_verified"
    password_change = "password_change"
    scenario_create = "scenario_create"
    scenario_update = "scenario_update"
    scenario_delete = "scenario_delete"
    scenario_deprecate = "scenario_deprecate"
    test_run_start = "test_run_start"
    subscription_change = "subscription_change"
    admin_action = "admin_action"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    event: Mapped[str] = mapped_column(Enum(AuditEvent), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), default="")
    entity_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ip: Mapped[str] = mapped_column(String(45), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    meta: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
