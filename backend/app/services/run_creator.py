"""Shared run-creation logic used by dashboard API and suites API."""
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import write_audit
from app.core.ssrf import assert_public_url
from app.models.test_run import RunStatus, TestRun
from app.models.user import User
from app.services.limits import (
    active_runs_count,
    can_run_tests,
    get_parallel_limit,
    global_active_runs_count,
    plan_of,
)
from app.services.time_prediction import predict_duration


def create_run_checked(db: Session, user: User, request: Request | None, *,
                       name: str, url: str, mode: str, script_text: str = "",
                       scenario_id: str | None = None, suite_id: str | None = None,
                       credential_id: str | None = None, timeout_minutes: int | None = None) -> TestRun:
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL должен начинаться с http:// или https://")

    try:
        assert_public_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    p = plan_of(user)
    if p == "blocked":
        raise HTTPException(status_code=403, detail="Триал-период закончился. Оформите подписку, чтобы запускать тесты.")
    if not can_run_tests(user):
        raise HTTPException(status_code=429, detail="Достигнут месячный лимит тестов. Оформите подписку для продолжения.")

    running = active_runs_count(db, user.id)
    if running >= get_parallel_limit(user):
        raise HTTPException(status_code=429, detail="Превышен лимит параллельных прогонов.")

    # Global cap: a set of authed users cannot together exceed the number of
    # worker slots / overwhelm the Docker daemon.
    if settings.GLOBAL_MAX_RUNS_ACTIVE and global_active_runs_count(db) >= settings.GLOBAL_MAX_RUNS_ACTIVE:
        raise HTTPException(status_code=429, detail="Сейчас выполняется максимально допустимое число тестов. Попробуйте позже.")

    num_steps = max(1, len([p for p in script_text.replace(";", ",").replace(".", ",").split(",") if p.strip()]))
    predicted = predict_duration(db, user.id, num_steps)

    run = TestRun(
        owner_id=user.id, scenario_id=scenario_id, name=name, url=url,
        mode=mode, script_text=script_text, status=RunStatus.queued,
        timeout_minutes=timeout_minutes or {"full_page": 15, "site": 60}.get(mode, 5),
        suite_id=suite_id, credential_id=credential_id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    from app.tasks.run_test import run_test_task

    task = run_test_task.delay(run.id, user.id, url, mode, script_text, name)
    run.celery_task_id = task.id
    db.commit()

    write_audit(db, user.id, "test_run_start", "test_run", run.id, request,
                meta={"mode": mode, "predicted_seconds": predicted})

    usage_percent_val = None
    from app.services.limits import usage_percent as _usage

    usage_percent_val = _usage(user)
    if usage_percent_val is not None and usage_percent_val >= 80:
        from app.tasks.notifications import send_limit_warning

        send_limit_warning.delay(user.id, int(usage_percent_val), is_100=usage_percent_val >= 100)

    return run
