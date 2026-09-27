"""Outgoing webhook delivery with retry."""
import hashlib
import hmac
import json
import logging

import httpx
from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)


def build_signature(payload: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def deliver_webhook(db: Session, webhook_id: str, payload: dict,
                    delivery: "WebhookDelivery | None" = None) -> bool:
    from app.models.webhook import WebhookConfig, WebhookDelivery

    cfg = db.query(WebhookConfig).filter(WebhookConfig.id == webhook_id).first()
    if not cfg or cfg.status != "active":
        return False
    body = json.dumps(payload, ensure_ascii=False)
    headers = {"Content-Type": "application/json"}
    if cfg.secret:
        headers["X-QAAgent-Signature"] = build_signature(body, cfg.secret)

    # Reuse an existing failed delivery for retries instead of duplicating rows.
    if delivery is None:
        delivery = db.query(WebhookDelivery).filter(
            WebhookDelivery.webhook_id == webhook_id,
            WebhookDelivery.success == False,  # noqa: E712
        ).order_by(WebhookDelivery.created_at.desc()).first()
    if delivery is None:
        delivery = WebhookDelivery(webhook_id=webhook_id, payload=payload)
        db.add(delivery)
    else:
        delivery.payload = payload
    db.commit()
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(cfg.url, content=body, headers=headers)
            delivery.response_code = resp.status_code
            delivery.success = 200 <= resp.status_code < 300
            if not delivery.success:
                delivery.error = f"HTTP {resp.status_code}"
            delivery.attempts += 1
    except Exception as exc:
        delivery.success = False
        delivery.error = str(exc)
        delivery.attempts += 1
        logger.error("Webhook delivery failed: %s", exc)
    db.add(delivery)
    db.commit()
    return delivery.success


def notify_test_finished(db: Session, owner_id: str, test_run_id: str, result: dict) -> None:
    from app.models.webhook import WebhookConfig

    configs = db.query(WebhookConfig).filter(
        WebhookConfig.owner_id == owner_id,
        WebhookConfig.event == "test_finished",
        WebhookConfig.status == "active",
    ).all()
    for cfg in configs:
        deliver_webhook(db, cfg.id, {
            "event": "test_finished",
            "test_run_id": test_run_id,
            "status": result.get("status"),
            "passed": result.get("passed_steps"),
            "failed": result.get("failed_steps"),
            "url": result.get("url"),
            "report_link": f"{settings.BACKEND_URL}/api/runs/{test_run_id}/report/html",
        })