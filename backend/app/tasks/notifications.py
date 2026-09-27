"""Notification / housekeeping tasks: limit warnings, renewal notices,
report cleanup, delivery retries with backoff."""
import logging
from datetime import datetime, timedelta, timezone

from app.database import SessionLocal
from app.services.messengers import send_telegram
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.notifications.send_limit_warning")
def send_limit_warning(user_id: str, pct: int, is_100: bool = False) -> None:
    from app.models.user import User
    from app.services.email import send_limit_notice_email

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return
        if user.email_consent or is_100:
            send_limit_notice_email(user.email, pct, is_100)
        if getattr(user, "telegram_chat_id", None):
            send_telegram(user.telegram_chat_id, f"QA Agent: использовано {pct}% лимита тестов")
    finally:
        db.close()


@celery_app.task(name="app.tasks.notifications.notify_upcoming_renewals")
def notify_upcoming_renewals() -> None:
    from app.models.payment import Subscription

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        soon = now + timedelta(days=3)
        subs = db.query(Subscription).filter(Subscription.status == "active",
                                             Subscription.auto_renew == True).all()  # noqa: E712
        for sub in subs:
            if sub.expires_at and now < sub.expires_at <= soon:
                from app.models.user import User
                from app.services.email import send_renewal_notice_email

                user = db.query(User).filter(User.id == sub.user_id).first()
                if user:
                    send_renewal_notice_email(user.email, 3)
    finally:
        db.close()


@celery_app.task(name="app.tasks.notifications.check_trial_expiry")
def check_trial_expiry() -> None:
    from app.models.user import User, Plan
    from app.config import settings

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        users = db.query(User).filter(User.plan.in_([Plan.trial.value, Plan.enterprise_trial.value])).all()
        for u in users:
            started = u.trial_started_at
            if not started:
                continue
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            days = settings.ENTERPRISE_TRIAL_DAYS if u.plan == Plan.enterprise_trial.value else settings.TRIAL_DAYS
            if started + timedelta(days=days) < now:
                u.plan = Plan.blocked.value
                db.add(u)
        db.commit()
    finally:
        db.close()


@celery_app.task(name="app.tasks.notifications.cleanup_old_reports")
def cleanup_old_reports() -> None:
    from app.models.test_run import TestRun

    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        old = db.query(TestRun).filter(TestRun.created_at < cutoff).all()
        for run in old:
            for art in run.artifacts:
                try:
                    import os

                    if os.path.exists(art.path):
                        os.remove(art.path)
                except Exception:
                    pass
            db.delete(run)
        db.commit()
    finally:
        db.close()


@celery_app.task(name="app.tasks.notifications.retry_deliveries")
def retry_deliveries() -> None:
    """Retry failed webhook deliveries with backoff (max 3 attempts)."""
    from app.models.webhook import WebhookDelivery

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        attempts_ok = {
            1: now - timedelta(minutes=5),
            2: now - timedelta(minutes=15),
            3: now - timedelta(minutes=60),
        }
        for attempt in (1, 2, 3):
            cutoff = attempts_ok[attempt]
            rows = db.query(WebhookDelivery).filter(
                WebhookDelivery.success == False,  # noqa: E712
                WebhookDelivery.attempts == attempt,
                WebhookDelivery.created_at < cutoff,
            ).all()
            for row in rows:
                if row.attempts >= 3:
                    row.error = "Доставка невозможна после 3 попыток"
                    continue
                from app.services.webhooks import deliver_webhook

                if row.payload:
                    deliver_webhook(db, row.webhook_id, row.payload, delivery=row)
    finally:
        db.close()