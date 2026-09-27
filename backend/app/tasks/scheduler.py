"""Scheduler: periodic checks for scheduled test jobs (Celery Beat)."""
import logging
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models.schedule import ScheduleJob, ScheduleRunLog, ScheduleStatus
from app.services.limits import can_run_tests
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.scheduler.check_schedules")
def check_schedules() -> None:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        jobs = db.query(ScheduleJob).filter(ScheduleJob.status == ScheduleStatus.active.value).all()
        for job in jobs:
            if not _is_due(job, now):
                continue
            user = _get_user(db, job.owner_id)
            if not user:
                continue
            if not can_run_tests(user):
                job.status = ScheduleStatus.limit_exceeded.value
                db.add(ScheduleRunLog(schedule_job_id=job.id, status="limit_exceeded"))
                db.commit()
                logger.info("Schedule %s marked limit_exceeded", job.id)
                continue
            _trigger_run(db, job)
    finally:
        db.close()


def _is_due(job: ScheduleJob, now: datetime) -> bool:
    last = job.last_run_at
    if not last:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if job.frequency == "hourly":
        return (now - last).total_seconds() >= 3600
    if job.frequency == "daily":
        return (now - last).total_seconds() >= 86400
    if job.frequency == "weekly":
        return (now - last).total_seconds() >= 604800
    return False


def _get_user(db, user_id):
    from app.models.user import User

    return db.query(User).filter(User.id == user_id).first()


def _trigger_run(db, job: ScheduleJob) -> None:
    from app.models.scenario import Scenario
    from app.models.test_run import RunStatus, TestRun

    scenario = db.query(Scenario).filter(Scenario.id == job.scenario_id).first()
    if not scenario:
        return
    run = TestRun(
        owner_id=job.owner_id,
        scenario_id=scenario.id,
        name=scenario.name,
        url=scenario.url,
        mode=scenario.mode.value if hasattr(scenario.mode, "value") else scenario.mode,
        script_text=scenario.script_text,
        status=RunStatus.queued,
        timeout_minutes=5 if scenario.mode == "specific" else 15,
    )
    db.add(run)
    db.commit()

    from app.tasks.run_test import run_test_task

    task = run_test_task.delay(run.id, job.owner_id, run.url,
                               run.mode, run.script_text, run.name)
    run.celery_task_id = task.id
    job.last_run_at = datetime.now(timezone.utc)
    db.add(ScheduleRunLog(schedule_job_id=job.id, test_run_id=run.id, status="queued"))
    db.commit()
    logger.info("Scheduled run triggered: run=%s job=%s", run.id, job.id)