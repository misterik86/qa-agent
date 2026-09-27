import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.api.deps import get_client_ip, get_current_user
from app.core.audit import write_audit
from app.core.security import (
    create_access_token,
    create_refresh_token,
    create_reset_token,
    decode_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.database import get_db
from app.models.user import Plan, RefreshToken, User, UserConsent
from app.schemas.auth import (
    ChangePassword,
    ForgotPassword,
    LogoutRequest,
    RefreshRequest,
    ResetPassword,
    TokenResponse,
    UserLogin,
    UserOut,
    UserRegister,
)
from app.services.email import send_reset_email, send_verification_email

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _issue_tokens(db: Session, user: User) -> TokenResponse:
    jti = str(uuid.uuid4())
    refresh_token = create_refresh_token(user.id, jti)
    expires = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    db.add(RefreshToken(user_id=user.id, token_hash=hash_token(refresh_token), expires_at=expires))
    db.commit()
    return TokenResponse(access_token=create_access_token(user.id), refresh_token=refresh_token)


def _as_aware(dt: datetime) -> datetime:
    """Normalize naive datetimes (SQLite) to aware UTC for comparison."""
    if dt is None:
        return dt
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@router.post("/register", status_code=201)
def register(body: UserRegister, request: Request, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == body.email.lower()).first()
    if existing:
        raise HTTPException(status_code=409, detail="Пользователь с таким email уже существует")
    if not body.accepted_tos or not body.accepted_privacy:
        raise HTTPException(status_code=400, detail="Необходимо принять пользовательское соглашение и политику конфиденциальности")

    user = User(
        email=body.email.lower(),
        full_name=body.full_name,
        password_hash=hash_password(body.password),
        plan=Plan.trial,
        trial_started_at=datetime.now(timezone.utc),
        email_consent=body.accepted_privacy,
        is_active=True,
    )
    db.add(user)
    db.commit()

    # Bootstrap admin: the very first registered user becomes admin;
    # emails listed in ADMIN_EMAILS always become admins.
    if not db.query(User).filter(User.is_admin == True).first() or user.email in settings.admin_emails:  # noqa: E712
        user.is_admin = True
        db.commit()

    db.add(UserConsent(user_id=user.id, accepted_tos=body.accepted_tos, accepted_privacy=body.accepted_privacy))
    db.commit()

    # verification token: 24h (not the 15-min password reset window)
    token = create_reset_token(user.id, str(uuid.uuid4()), minutes=60 * 24)
    if settings.DEV_AUTO_VERIFY:
        # dev mode: skip SMTP, confirm email right away
        user.is_verified = True
        db.commit()
        write_audit(db, user.id, "register", request=request)
        return {"message": "Регистрация успешна (dev-режим: email подтверждён автоматически)."}

    send_verification_email(user.email, token)
    write_audit(db, user.id, "register", "user", user.id, request)
    return {"message": "Регистрация успешна. Проверьте email для подтверждения."}


@router.get("/verify-email")
def verify_email(token: str, db: Session = Depends(get_db)):
    payload = decode_token(token, "reset")
    if not payload:
        raise HTTPException(status_code=400, detail="Недействительная ссылка подтверждения")
    user = db.query(User).filter(User.id == payload.get("sub")).first()
    if not user:
        raise HTTPException(status_code=400, detail="Пользователь не найден")
    user.is_verified = True
    db.commit()
    return {"message": "Email подтверждён. Теперь вы можете войти."}


@router.post("/login", response_model=TokenResponse)
def login(body: UserLogin, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный email или пароль")
    if not user.is_verified:
        raise HTTPException(status_code=403, detail="Подтвердите email перед входом")
    if user.plan == Plan.blocked:
        raise HTTPException(status_code=403, detail="Аккаунт заблокирован администратором")
    write_audit(db, user.id, "login", request=request)
    return _issue_tokens(db, user)


@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    payload = decode_token(body.refresh_token, "refresh")
    if not payload:
        raise HTTPException(status_code=401, detail="Недействительный refresh-токен")
    token_row = db.query(RefreshToken).filter(
        RefreshToken.token_hash == hash_token(body.refresh_token)
    ).first()
    if not token_row or token_row.revoked:
        raise HTTPException(status_code=401, detail="Refresh-токен отозван")
    if token_row.expires_at and _as_aware(token_row.expires_at) < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Refresh-токен истёк")
    # rotation: revoke old
    token_row.revoked = True
    db.commit()
    user = db.query(User).filter(User.id == payload.get("sub")).first()
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    return _issue_tokens(db, user)


@router.post("/logout")
def logout(body: LogoutRequest, db: Session = Depends(get_db)):
    if body.refresh_token:
        row = db.query(RefreshToken).filter(
            RefreshToken.token_hash == hash_token(body.refresh_token)
        ).first()
        if row:
            row.revoked = True
            db.commit()
    return {"message": "Выход выполнен"}


@router.post("/forgot-password")
def forgot_password(body: ForgotPassword, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if user:
        token = create_reset_token(user.id, str(uuid.uuid4()))
        send_reset_email(user.email, token)
    return {"message": "Если email существует, ссылка для сброса отправлена"}


@router.post("/reset-password")
def reset_password(body: ResetPassword, db: Session = Depends(get_db)):
    payload = decode_token(body.token, "reset")
    if not payload:
        raise HTTPException(status_code=400, detail="Недействительная или истёкшая ссылка")
    user = db.query(User).filter(User.id == payload.get("sub")).first()
    if not user:
        raise HTTPException(status_code=400, detail="Пользователь не найден")
    user.password_hash = hash_password(body.new_password)
    # revoke all refresh tokens
    for t in db.query(RefreshToken).filter(RefreshToken.user_id == user.id).all():
        t.revoked = True
    db.commit()
    return {"message": "Пароль изменён"}


@router.post("/change-password")
def change_password(body: ChangePassword, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user), request: Request = None):
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Неверный текущий пароль")
    user.password_hash = hash_password(body.new_password)
    for t in db.query(RefreshToken).filter(RefreshToken.user_id == user.id).all():
        t.revoked = True
    db.commit()
    write_audit(db, user.id, "password_change", request=request)
    return {"message": "Пароль изменён"}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services.limits import usage_percent

    out = UserOut(
        id=user.id, email=user.email, full_name=user.full_name,
        is_verified=user.is_verified, is_admin=user.is_admin, plan=user.plan,
        plan_expires_at=user.plan_expires_at, locale=user.locale,
        usage_percent=usage_percent(user),
    )
    return out