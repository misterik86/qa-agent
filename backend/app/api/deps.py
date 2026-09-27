from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.database import get_db
from app.models.user import ApiKey, Plan, User

bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(status_code=401, detail="Не авторизован")
    payload = decode_token(credentials.credentials, "access")
    if not payload:
        raise HTTPException(status_code=401, detail="Недействительный токен")
    user = db.query(User).filter(User.id == payload.get("sub")).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    if user.plan == Plan.blocked:
        raise HTTPException(status_code=403, detail="Аккаунт заблокирован администратором")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    return user


def get_current_user_or_none(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User | None:
    try:
        return get_current_user(credentials, db)
    except HTTPException:
        return None


def authenticate_api_key(
    x_api_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Требуется API-ключ (X-API-Key)")
    from datetime import datetime, timezone

    from app.core.security import hash_token

    key_hash = hash_token(x_api_key)
    api_key = db.query(ApiKey).filter(ApiKey.key_hash == key_hash, ApiKey.active == True).first()  # noqa: E712
    if not api_key:
        raise HTTPException(status_code=401, detail="Недействительный API-ключ")
    api_key.last_used_at = datetime.now(timezone.utc)
    db.commit()
    user = db.query(User).filter(User.id == api_key.user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Владелец ключа не найден")
    return user


def get_client_ip(request: Request) -> str:
    if request.client:
        return request.client.host
    return "0.0.0.0"