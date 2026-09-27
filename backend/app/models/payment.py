import enum
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Enum, ForeignKey, Boolean, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SubscriptionPlan(str, enum.Enum):
    pro = "pro"
    enterprise = "enterprise"


class SubscriptionStatus(str, enum.Enum):
    active = "active"
    cancelled = "cancelled"
    expired = "expired"
    pending = "pending"


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    plan: Mapped[str] = mapped_column(Enum(SubscriptionPlan), nullable=False)
    status: Mapped[str] = mapped_column(Enum(SubscriptionStatus), default=SubscriptionStatus.active)
    payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    yookassa_payment_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    plan: Mapped[str] = mapped_column(Enum(SubscriptionPlan), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    status: Mapped[str] = mapped_column(String(30), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
