import enum
import uuid
from datetime import datetime

from sqlalchemy import String, Text, Integer, DateTime, Enum, ForeignKey, JSON, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WebhookEvent(str, enum.Enum):
    test_finished = "test_finished"


class WebhookStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"


class WebhookConfig(Base):
    __tablename__ = "webhook_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    event: Mapped[str] = mapped_column(Enum(WebhookEvent), default=WebhookEvent.test_finished)
    secret: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(Enum(WebhookStatus), default=WebhookStatus.active)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    webhook_id: Mapped[str] = mapped_column(String(36), ForeignKey("webhook_configs.id"), index=True, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
