"""Admin panel API: users, runs, payments, audit, stats."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.audit import write_audit
from app.database import get_db
from app.models.audit import AuditLog
from app.models.payment import Payment
from app.models.test_run import RunStatus, TestRun
from app.models.user import Plan, User
from app.schemas.admin import (
    AdminAuditOut,
    AdminPaymentOut,
    AdminRunOut,
    AdminStats,
    AdminUserOut,
    AdminUserPatch,
)

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

ALLOWED_PLANS = {"trial", "pro", "enterprise", "enterprise_trial", "blocked"}


def _get_user_or_404(db: Session, user_id: str) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return user


def _user_out(db: Session, u: User) -> AdminUserOut:
    runs_count = db.query(TestRun).filter(TestRun.owner_id == u.id).count()
    return AdminUserOut(
        id=u.id,
        email=u.email,
        full_name=u.full_name,
        plan=u.plan.value if hasattr(u.plan, "value") else str(u.plan),
        plan_expires_at=u.plan_expires_at,
        is_verified=u.is_verified,
        is_admin=u.is_admin,
        tests_used_this_month=u.tests_used_this_month or 0,
        runs_count=runs_count,
        created_at=u.created_at,
    )


@router.get("/stats", response_model=AdminStats)
def admin_stats(db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(days=1)

    users_total = db.query(User).count()
    users_verified = db.query(User).filter(User.is_verified == True).count()  # noqa: E712
    users_blocked = db.query(User).filter(User.plan == Plan.blocked).count()
    users_admins = db.query(User).filter(User.is_admin == True).count()  # noqa: E712
    users_new_today = db.query(User).filter(User.created_at >= day_ago).count()

    runs_total = db.query(TestRun).count()
    runs_passed = db.query(TestRun).filter(TestRun.status == RunStatus.passed).count()
    runs_failed = db.query(TestRun).filter(TestRun.status == RunStatus.failed).count()
    runs_active = db.query(TestRun).filter(
        TestRun.status.in_([RunStatus.queued.value, RunStatus.running.value])
    ).count()
    runs_today = db.query(TestRun).filter(TestRun.created_at >= day_ago).count()

    payments_total = db.query(Payment).count()
    payments_sum = db.query(sa_func.coalesce(sa_func.sum(Payment.amount), 0)).filter(
        Payment.status == "succeeded"
    ).scalar()

    return AdminStats(
        users_total=users_total,
        users_verified=users_verified,
        users_blocked=users_blocked,
        users_admins=users_admins,
        users_new_today=users_new_today,
        runs_total=runs_total,
        runs_passed=runs_passed,
        runs_failed=runs_failed,
        runs_active=runs_active,
        runs_today=runs_today,
        payments_total=payments_total,
        payments_sum=int(payments_sum or 0),
    )


@router.get("/users", response_model=list[AdminUserOut])
def admin_users(
    search: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    q = db.query(User)
    if search:
        like = f"%{search.lower()}%"
        q = q.filter((sa_func.lower(User.email).like(like)) | (sa_func.lower(User.full_name).like(like)))
    users = q.order_by(User.created_at.desc()).offset(offset).limit(limit).all()
    return [_user_out(db, u) for u in users]


@router.patch("/users/{user_id}", response_model=AdminUserOut)
def admin_update_user(
    user_id: str,
    body: AdminUserPatch,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    target = _get_user_or_404(db, user_id)
    changes: list[str] = []

    if body.is_admin is not None:
        if target.id == admin.id and body.is_admin is False:
            raise HTTPException(status_code=400, detail="Нельзя снять права администратора с себя")
        target.is_admin = body.is_admin
        changes.append(f"is_admin={body.is_admin}")

    if body.plan is not None:
        if target.id == admin.id and body.plan == "blocked":
            raise HTTPException(status_code=400, detail="Нельзя заблокировать самого себя")
        try:
            target.plan = Plan(body.plan)
        except ValueError:
            raise HTTPException(status_code=400, detail="Недопустимый тариф")
        if body.plan != "blocked" and target.plan_expires_at and target.plan_expires_at < datetime.now(timezone.utc):
            target.plan_expires_at = None
        if body.plan == "trial":
            target.trial_started_at = datetime.now(timezone.utc)
        changes.append(f"plan={body.plan}")

    if body.is_verified is not None:
        target.is_verified = body.is_verified
        changes.append(f"is_verified={body.is_verified}")

    if body.full_name is not None:
        target.full_name = body.full_name
        changes.append("full_name")

    db.commit()
    if changes:
        write_audit(db, admin.id, "admin_action", "user", target.id,
                    meta={"admin_email": admin.email, "changes": changes})
    return _user_out(db, target)


@router.get("/runs", response_model=list[AdminRunOut])
def admin_runs(
    status: str | None = Query(default=None, pattern="^(queued|running|passed|failed|cancelled)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    q = db.query(TestRun).join(User, TestRun.owner_id == User.id)
    if status:
        q = q.filter(TestRun.status == status)
    runs = q.order_by(TestRun.created_at.desc()).offset(offset).limit(limit).all()
    out = []
    for r in runs:
        item = AdminRunOut(
            id=r.id,
            owner_email=r.owner.email if r.owner else "",
            url=r.url,
            mode=r.mode.value if hasattr(r.mode, "value") else str(r.mode),
            status=r.status.value if hasattr(r.status, "value") else str(r.status),
            total_steps=r.total_steps,
            passed_steps=r.passed_steps,
            failed_steps=r.failed_steps,
            created_at=r.created_at,
            finished_at=r.finished_at,
        )
        out.append(item)
    return out


@router.get("/payments", response_model=list[AdminPaymentOut])
def admin_payments(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(Payment, User.email)
        .join(User, Payment.user_id == User.id)
        .order_by(Payment.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        AdminPaymentOut(
            id=p.id,
            user_email=email,
            plan=p.plan.value if hasattr(p.plan, "value") else str(p.plan),
            amount=p.amount,
            currency=p.currency,
            status=p.status,
            created_at=p.created_at,
        )
        for p, email in rows
    ]


@router.get("/audit", response_model=list[AdminAuditOut])
def admin_audit(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(AuditLog, User.email)
        .outerjoin(User, AuditLog.user_id == User.id)
        .order_by(AuditLog.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        AdminAuditOut(
            id=log.id,
            user_email=email,
            event=log.event.value if hasattr(log.event, "value") else str(log.event),
            entity_type=log.entity_type,
            entity_id=log.entity_id,
            ip=log.ip,
            meta=log.meta,
            created_at=log.created_at,
        )
        for log, email in rows
    ]
