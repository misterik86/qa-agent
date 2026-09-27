import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, Integer, DateTime, Enum, ForeignKey, Boolean, Float, JSON, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    passed = "passed"
    failed = "failed"
    cancelled = "cancelled"
    timeout = "timeout"
    limit_exceeded = "limit_exceeded"
    error = "error"


class StepStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    passed = "passed"
    failed = "failed"
    skipped = "skipped"


class RunMode(str, enum.Enum):
    specific = "specific"
    full_page = "full_page"
    site = "site"  # crawl: BFS over same-host internal pages


class TestRun(Base):
    __tablename__ = "test_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    scenario_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("scenarios.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    mode: Mapped[str] = mapped_column(Enum(RunMode), default=RunMode.specific)
    script_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Enum(RunStatus), default=RunStatus.queued)
    container_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    total_steps: Mapped[int] = mapped_column(Integer, default=0)
    passed_steps: Mapped[int] = mapped_column(Integer, default=0)
    failed_steps: Mapped[int] = mapped_column(Integer, default=0)
    console_errors: Mapped[str] = mapped_column(Text, default="")
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timeout_minutes: Mapped[int] = mapped_column(Integer, default=5)
    error_message: Mapped[str] = mapped_column(Text, default="")
    suite_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    credential_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    share_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Python-side default: microsecond precision (SQLite CURRENT_TIMESTAMP has 1s resolution,
    # which breaks run-ordering comparisons for rapid successive runs).
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    owner = relationship("User", back_populates="test_runs")
    scenario = relationship("Scenario", back_populates="test_runs")
    steps = relationship("TestStep", back_populates="test_run", cascade="all, delete-orphan")
    artifacts = relationship("TestRunArtifact", back_populates="test_run", cascade="all, delete-orphan")

    @property
    def duration_seconds(self) -> float:
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return 0.0


class TestStep(Base):
    __tablename__ = "test_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    test_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("test_runs.id"), index=True, nullable=False)
    order: Mapped[int] = mapped_column(Integer, default=0)
    action: Mapped[str] = mapped_column(String(50), default="")
    selector: Mapped[str] = mapped_column(String(512), default="")
    value: Mapped[str] = mapped_column(Text, default="")
    expected: Mapped[str] = mapped_column(Text, default="")
    test_case_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(Enum(StepStatus), default=StepStatus.pending)
    message: Mapped[str] = mapped_column(Text, default="")
    screenshot_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    console_error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    test_run = relationship("TestRun", back_populates="steps")


class TestRunArtifact(Base):
    __tablename__ = "test_run_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    test_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("test_runs.id"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # report | screenshot
    format: Mapped[str] = mapped_column(String(20), nullable=False)  # pdf | html | json | junit | png
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    test_run = relationship("TestRun", back_populates="artifacts")


class TimePrediction(Base):
    __tablename__ = "time_predictions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    test_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("test_runs.id"), index=True, nullable=False)
    predicted_seconds: Mapped[int] = mapped_column(Integer, default=0)
    actual_seconds: Mapped[int] = mapped_column(Integer, default=0)
    base_seconds: Mapped[int] = mapped_column(Integer, default=5)
    steps_seconds: Mapped[int] = mapped_column(Integer, default=0)
    elements_seconds: Mapped[int] = mapped_column(Integer, default=0)
    correction_factor: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
