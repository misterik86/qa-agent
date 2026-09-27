"""Subscription / quota / trial logic."""
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import User


def plan_of(user: User) -> str:
    now = datetime.now(timezone.utc)
    if user.plan_expires_at and user.plan_expires_at < now:
        return "blocked"
    # Trial auto-expiry enforced here (belt-and-braces with the beat task).
    if user.plan == "trial" and user.trial_started_at:
        start = user.trial_started_at
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if start + timedelta(days=settings.TRIAL_DAYS) < now:
            return "blocked"
    if user.plan == "enterprise_trial" and user.trial_started_at:
        start = user.trial_started_at
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if start + timedelta(days=settings.ENTERPRISE_TRIAL_DAYS) < now:
            return "blocked"
    return user.plan


def get_test_limit(user: User) -> int | None:
    p = plan_of(user)
    if p in ("pro",):
        return settings.PRO_TESTS_MONTHLY
    if p in ("enterprise",):
        return None
    if p in ("enterprise_trial",):
        return settings.ENTERPRISE_TRIAL_TESTS
    if p == "trial":
        return settings.TRIAL_TESTS_MAX
    return 0  # blocked


def get_parallel_limit(user: User) -> int:
    p = plan_of(user)
    if p == "pro":
        return settings.PRO_PARALLEL
    if p == "enterprise":
        return settings.ENTERPRISE_PARALLEL
    if p in ("trial", "enterprise_trial"):
        return 1
    return 0


def can_run_tests(user: User) -> bool:
    p = plan_of(user)
    if p == "blocked":
        return False
    limit = get_test_limit(user)
    if limit is None:
        return True  # enterprise unlimited
    used = monthly_used(user)
    return used < limit


def monthly_used(user: User) -> int:
    current = datetime.now(timezone.utc).strftime("%Y-%m")
    if user.quota_month != current:
        return 0
    return user.tests_used_this_month


def usage_percent(user: User) -> float | None:
    limit = get_test_limit(user)
    if limit is None:
        return None
    return min(100.0, round(monthly_used(user) / limit * 100, 1)) if limit else 0.0


def register_test_use(db: Session, user: User) -> None:
    current = datetime.now(timezone.utc).strftime("%Y-%m")
    if user.quota_month != current:
        user.quota_month = current
        user.tests_used_this_month = 0
    user.tests_used_this_month += 1
    db.commit()


def is_trial_active(user: User) -> bool:
    now = datetime.now(timezone.utc)
    start = user.trial_started_at
    if not start:
        return False
    return user.plan == "trial" and now < start + timedelta(days=settings.TRIAL_DAYS)


def active_runs_count(db: Session, user_id: str) -> int:
    from app.models.test_run import RunStatus, TestRun

    return db.query(TestRun).filter(
        TestRun.owner_id == user_id,
        TestRun.status.in_([RunStatus.queued.value, RunStatus.running.value]),
    ).count()


def global_active_runs_count(db: Session) -> int:
    """Total queued/running runs across all users (global parallel cap)."""
    from app.models.test_run import RunStatus, TestRun

    return db.query(TestRun).filter(
        TestRun.status.in_([RunStatus.queued.value, RunStatus.running.value]),
    ).count()