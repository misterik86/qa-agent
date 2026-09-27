"""YooKassa (ЮKassa) payments integration."""
import base64
import ipaddress
import json
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

PLAN_PRICES = {"pro": 3500, "enterprise": 15000}

# YooKassa sends webhook notifications from these IP ranges (documented).
# https://yookassa.ru/developers/using-api/interaction-format#notifications
YOOKASSA_WEBHOOK_NETS = [
    "76.76.244.0/24",
    "185.71.214.0/24",
    "185.71.216.0/24",
    "130.193.70.0/24",
    "130.193.71.0/24",
    "213.158.192.0/21",
]


def _auth() -> str:
    raw = f"{settings.YOOKASSA_SHOP_ID}:{settings.YOOKASSA_SECRET_KEY}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("utf-8")


def create_payment(user_id: str, plan: str, amount: int | None = None,
                   description: str = "Подписка QA Agent") -> dict:
    """Create payment; in test mode returns a fake confirmation_url."""
    if settings.YOOKASSA_TEST_MODE or not settings.YOOKASSA_SHOP_ID:
        return {
            "id": f"test_pay_{user_id[:8]}",
            "confirmation_url": f"{settings.BACKEND_URL}/api/payments/mock-confirm?plan={plan}&user={user_id}",
            "status": "pending",
            "test_mode": True,
        }
    url = "https://api.yookassa.ru/v3/payments"
    payload = {
        "amount": {"value": str(amount or PLAN_PRICES.get(plan, 0)), "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": f"{settings.FRONTEND_URL}/dashboard?paid=1"},
        "description": description,
        "metadata": {"user_id": user_id, "plan": plan},
    }
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(url, json=payload, headers={
                "Authorization": _auth(),
                "Idempotence-Key": f"{user_id}-{plan}-{json.dumps(payload)[:50]}",
            })
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.error("YooKassa create payment failed: %s", exc)
        raise

    # Persist the payment so incoming webhooks can be validated against it.
    try:
        from datetime import datetime, timezone

        from app.database import SessionLocal
        from app.models.payment import Payment

        db = SessionLocal()
        try:
            db.add(Payment(
                user_id=user_id,
                yookassa_payment_id=data.get("id", ""),
                plan=plan,
                amount=int(payload["amount"]["value"]),
                status=data.get("status", "pending"),
                created_at=datetime.now(timezone.utc),
            ))
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        logger.error("Failed to persist payment record: %s", exc)
    return data


def verify_webhook_signature(headers: dict, body: bytes) -> bool:
    """Validate an incoming YooKassa webhook.

    YooKassa does not sign webhook bodies; authenticity is ensured by:
      1. HTTPS + sender IP within YooKassa documented ranges (production),
      2. JSON body validity,
      3. the payment existing in our database (i.e. we actually created it).
    """
    try:
        json.loads(body.decode("utf-8"))
    except Exception:
        return False

    if settings.YOOKASSA_TEST_MODE or not settings.YOOKASSA_SHOP_ID:
        return True  # test mode: JSON check is enough

    # Sender IP check (production) — a missing header is itself a red flag.
    sender = (headers.get("x-real-ip") or headers.get("x-forwarded-for") or headers.get("x-remote-ip") or "").split(",")[0].strip()
    if not sender:
        logger.warning("YooKassa webhook rejected: no sender IP header present")
        return False
    try:
        ip = ipaddress.ip_address(sender)
        if not any(ip in ipaddress.ip_network(net) for net in YOOKASSA_WEBHOOK_NETS):
            logger.warning("YooKassa webhook rejected: sender IP %s not in trusted ranges", sender)
            return False
    except ValueError:
        logger.warning("YooKassa webhook rejected: invalid sender IP %r", sender)
        return False

    # Payment existence check (production)
    data = json.loads(body.decode("utf-8"))
    payment_id = (data.get("object") or {}).get("id", "")
    if payment_id:
        try:
            from app.database import SessionLocal
            from app.models.payment import Payment

            db = SessionLocal()
            try:
                known = db.query(Payment).filter(Payment.yookassa_payment_id == payment_id).first()
            finally:
                db.close()
            if not known:
                logger.warning("YooKassa webhook rejected: unknown payment id %s", payment_id)
                return False
        except Exception as exc:
            logger.error("YooKassa webhook payment lookup failed: %s", exc)
            return False
    return True


def deactivate_subscription(user_id: str) -> None:
    """Downgrade/block subscription after payment cancellation or refund."""
    from datetime import datetime, timezone

    from app.database import SessionLocal
    from app.models.payment import Subscription, SubscriptionStatus
    from app.models.user import Plan as UserPlan, User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return
        sub = db.query(Subscription).filter(
            Subscription.user_id == user_id, Subscription.status == SubscriptionStatus.active.value).first()
        if sub:
            sub.status = SubscriptionStatus.cancelled.value
            sub.cancelled_at = datetime.now(timezone.utc)
        user.plan = UserPlan.trial
        user.plan_expires_at = None
        db.commit()
        logger.info("Subscription deactivated: user=%s", user_id)
    finally:
        db.close()


def activate_subscription(user_id: str, plan: str, payment_id: str) -> None:
    """Activate subscription after successful payment (called from webhook)."""
    from datetime import datetime, timedelta, timezone

    from app.models.payment import Subscription
    from app.models.user import Plan as UserPlan
    from app.database import SessionLocal

    if plan not in PLAN_PRICES:
        raise ValueError(f"Недопустимый тариф: {plan!r}")

    db = SessionLocal()
    try:
        from app.models.user import User

        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return
        expires = datetime.now(timezone.utc) + timedelta(days=30)

        # Idempotent: reuse existing active subscription for this payment.
        sub = db.query(Subscription).filter(
            Subscription.payment_id == payment_id).first()
        if not sub:
            sub = db.query(Subscription).filter(
                Subscription.user_id == user_id,
                Subscription.status == "active").first()
        if sub:
            sub.plan = plan
            sub.status = "active"
            sub.payment_id = payment_id
            sub.expires_at = expires
        else:
            sub = Subscription(
                user_id=user_id,
                plan=plan,
                status="active",
                payment_id=payment_id,
                expires_at=expires,
            )
            db.add(sub)
        db.flush()
        user.plan = UserPlan.pro if plan == "pro" else UserPlan.enterprise
        user.plan_expires_at = expires
        user.subscription_id = sub.id
        db.commit()
        logger.info("Subscription activated: user=%s plan=%s", user_id, plan)
    finally:
        db.close()