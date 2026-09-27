from celery import Celery

from app.config import settings

celery_app = Celery(
    "qa_agent",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.run_test", "app.tasks.scheduler", "app.tasks.notifications"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
)

if settings.CELERY_ALWAYS_EAGER:
    celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)

celery_app.conf.update(
    beat_schedule={
        "check-schedules": {
            "task": "app.tasks.scheduler.check_schedules",
            "schedule": 60.0,  # every minute
        },
        "notify-renewals": {
            "task": "app.tasks.notifications.notify_upcoming_renewals",
            "schedule": 86400.0,  # daily
        },
        "check-trial-expiry": {
            "task": "app.tasks.notifications.check_trial_expiry",
            "schedule": 3600.0,  # hourly
        },
        "retry-deliveries": {
            "task": "app.tasks.notifications.retry_deliveries",
            "schedule": 900.0,  # every 15 minutes
        },
        "cleanup-old-reports": {
            "task": "app.tasks.notifications.cleanup_old_reports",
            "schedule": 86400.0,  # daily
        },
    },
)