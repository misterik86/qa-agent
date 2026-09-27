import enum
import uuid
from datetime import datetime

from sqlalchemy import String, Text, Integer, DateTime, Enum, ForeignKey, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ScheduleFrequency(str, enum.Enum):
    hourly = "hourly"
    daily = "daily"
    weekly = "weekly"


class DeliveryChannel(str, enum.Enum):
    telegram = "telegram"
    vk = "vk"
    email = "email"


class ScheduleStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    limit_exceeded = "limit_exceeded"


class ScheduleJob(Base):
    __tablename__ = "schedule_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    scenario_id: Mapped[str] = mapped_column(String(36), ForeignKey("scenarios.id"), index=True, nullable=False)
    frequency: Mapped[str] = mapped_column(Enum(ScheduleFrequency), nullable=False)
    channel: Mapped[str] = mapped_column(Enum(DeliveryChannel), nullable=False)
    chat_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vk_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vk_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_recipient: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(Enum(ScheduleStatus), default=ScheduleStatus.active)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())


class ScheduleRunLog(Base):
    __tablename__ = "schedule_run_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    schedule_job_id: Mapped[str] = mapped_column(String(36), ForeignKey("schedule_jobs.id"), index=True, nullable=False)
    test_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="sent")  # sent | failed | retrying | limit_exceeded
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
