import enum
import uuid
from datetime import datetime

from sqlalchemy import String, Text, Integer, DateTime, Enum, ForeignKey, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ScenarioStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    deprecated = "deprecated"
    failed = "failed"


class ScenarioMode(str, enum.Enum):
    specific = "specific"
    full_page = "full_page"


class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    mode: Mapped[str] = mapped_column(Enum(ScenarioMode), default=ScenarioMode.specific)
    script_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Enum(ScenarioStatus), default=ScenarioStatus.draft)
    tags: Mapped[str] = mapped_column(String(255), default="")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    template_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), onupdate=func.now())

    owner = relationship("User", back_populates="scenarios")
    versions = relationship(
        "ScenarioVersion",
        back_populates="scenario",
        cascade="all, delete-orphan",
        order_by="ScenarioVersion.version",
    )
    test_runs = relationship("TestRun", back_populates="scenario", cascade="all, delete-orphan")


class ScenarioVersion(Base):
    __tablename__ = "scenario_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    scenario_id: Mapped[str] = mapped_column(String(36), ForeignKey("scenarios.id"), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    author_id: Mapped[str] = mapped_column(String(36), nullable=False)
    change_comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    scenario = relationship("Scenario", back_populates="versions")
