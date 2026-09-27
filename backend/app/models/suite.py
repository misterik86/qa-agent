import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, ForeignKey, Boolean, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SiteCredential(Base):
    """Stored login credentials for the site under test (password encrypted)."""
    __tablename__ = "site_credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False, default="Доступ")
    login_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    username: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False, default="")
    username_selector: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    password_selector: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    submit_selector: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())


class TestSuite(Base):
    """A persistent regression set of test cases for one site."""
    __tablename__ = "test_suites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="specific")
    credential_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("site_credentials.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), onupdate=func.now())

    cases = relationship("TestCase", back_populates="suite",
                         cascade="all, delete-orphan", order_by="TestCase.order")
    credential = relationship("SiteCredential")


class TestCase(Base):
    """One persistent test case: a single step template with expectation."""
    __tablename__ = "test_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    suite_id: Mapped[str] = mapped_column(String(36), ForeignKey("test_suites.id"), index=True, nullable=False)
    order: Mapped[int] = mapped_column(Integer, default=0)
    action: Mapped[str] = mapped_column(String(50), nullable=False, default="click")
    selector: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    expected: Mapped[str] = mapped_column(Text, nullable=False, default="")
    expected_effect: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="auto")  # auto | manual
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    suite = relationship("TestSuite", back_populates="cases")

    def to_step(self) -> dict:
        return {
            "action": self.action,
            "selector": self.selector,
            "value": self.value,
            "expected": self.expected,
            "expected_effect": self.expected_effect,
        }
