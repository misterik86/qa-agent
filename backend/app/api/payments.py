import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import settings
from app.database import get_db
from app.models.user import User
from app.schemas.misc import PaymentCreate
from app.services.payments import PLAN_PRICES, create_payment, verify_webhook_signature

router = APIRouter(prefix="/api/payments", tags=["payments"])


@router.post("/create")
def create(body: PaymentCreate, user: User = Depends(get_current_user)):
    if body.plan not in ("pro", "enterprise"):
        raise HTTPException(status_code=400, detail="Недопустимый тариф")
    payment = create_payment(user.id, body.plan, PLAN_PRICES.get(body.plan))
    return {
        "payment_id": payment["id"],
        "confirmation_url": payment.get("confirmation_url"),
        "status": payment.get("status"),
        "test_mode": payment.get("test_mode", False),
    }


@router.post("/mock-confirm")
def mock_confirm(plan: str, user: User = Depends(get_current_user)):
    """Dev-only: activate subscription without real payment (YooKassa test mode)."""
    if not settings.YOOKASSA_TEST_MODE:
        raise HTTPException(status_code=403, detail="Тестовый режим отключён")
    if plan not in ("pro", "enterprise"):
        raise HTTPException(status_code=400, detail="Недопустимый тариф")
    from app.services.payments import activate_subscription

    activate_subscription(user.id, plan, f"mock_{user.id}")
    return {"message": f"Подписка {plan} активирована (тестовый режим)"}


@router.post("/webhook")
async def yookassa_webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    if not verify_webhook_signature(dict(request.headers), body):
        raise HTTPException(status_code=400, detail="Недействительная подпись вебхука")
    try:
        data = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Некорректный JSON")

    event = data.get("event", "")
    obj = data.get("object", {})
    payment_status = obj.get("status", "")
    metadata = obj.get("metadata", {})
    user_id = metadata.get("user_id", "")
    plan = metadata.get("plan", "pro")

    if event in ("payment.succeeded",) and payment_status == "succeeded" and user_id:
        from app.models.payment import Payment
        from app.services.payments import activate_subscription

        # Cross-check: the payment we created must belong to this user.
        payment_id = obj.get("id", "")
        known = db.query(Payment).filter(Payment.yookassa_payment_id == payment_id).first()
        if known and known.user_id != user_id:
            raise HTTPException(status_code=400, detail="Платёж не принадлежит пользователю")
        if known:
            known.status = "succeeded"
            db.commit()
        activate_subscription(user_id, plan, payment_id)
        return {"message": "ok"}

    if event in ("payment.canceled", "refund.succeeded") and user_id:
        from app.services.payments import deactivate_subscription

        deactivate_subscription(user_id)
        return {"message": "ok"}

    return {"message": "ignored"}


@router.get("/status")
def payment_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models.payment import Subscription

    sub = db.query(Subscription).filter(Subscription.user_id == user.id,
                                        Subscription.status == "active").first()
    if not sub:
        return {"active": False, "plan": user.plan}
    return {
        "active": True,
        "plan": sub.plan.value if hasattr(sub.plan, "value") else sub.plan,
        "expires_at": sub.expires_at,
        "auto_renew": sub.auto_renew,
    }