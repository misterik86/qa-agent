import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Boolean, DateTime, Integer, Enum, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Plan(str, enum.Enum):
    trial = "trial"
    pro = "pro"
    enterprise = "enterprise"
    enterprise_trial = "enterprise_trial"
    blocked = "blocked"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    plan: Mapped[str] = mapped_column(Enum(Plan), default=Plan.trial)
    trial_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    plan_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    subscription_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tests_used_this_month: Mapped[int] = mapped_column(Integer, default=0)
    quota_month: Mapped[str] = mapped_column(String(7), default=lambda: datetime.now(timezone.utc).strftime("%Y-%m"))
    email_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    locale: Mapped[str] = mapped_column(String(5), default="ru")
    telegram_chat_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), onupdate=func.now())

    scenarios = relationship("Scenario", back_populates="owner", cascade="all, delete-orphan")
    test_runs = relationship("TestRun", back_populates="owner", cascade="all, delete-orphan")
    refresh_tokens = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    user = relationship("User", back_populates="refresh_tokens")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), default="Default")
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserConsent(Base):
    __tablename__ = "user_consents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    accepted_tos: Mapped[bool] = mapped_column(Boolean, default=False)
    accepted_privacy: Mapped[bool] = mapped_column(Boolean, default=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
