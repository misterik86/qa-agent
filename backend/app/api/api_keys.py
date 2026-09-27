import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import authenticate_api_key, get_current_user
from app.core.security import hash_token
from app.database import get_db
from app.models.user import ApiKey, User
from app.schemas.misc import ApiKeyCreate, ApiKeyOut
from app.schemas.run import TestRunCreate, TestRunOut
from app.services.limits import can_run_tests

router = APIRouter(prefix="/api/keys", tags=["api_keys"])


def _to_out(k: ApiKey, full_key: str | None = None) -> ApiKeyOut:
    return ApiKeyOut(
        id=k.id, name=k.name, prefix=k.prefix, active=k.active,
        created_at=k.created_at, key=full_key,
    )


@router.post("", response_model=ApiKeyOut, status_code=201)
def create_key(body: ApiKeyCreate, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    key = "qa_" + secrets.token_urlsafe(32)
    prefix = key[:12]
    api_key = ApiKey(user_id=user.id, name=body.name, key_hash=hash_token(key), prefix=prefix)
    db.add(api_key)
    db.commit()
    db.refresh(api_key)
    return _to_out(api_key, key)


@router.get("", response_model=list[ApiKeyOut])
def list_keys(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    keys = db.query(ApiKey).filter(ApiKey.user_id == user.id).all()
    return [_to_out(k) for k in keys]


@router.delete("/{key_id}", status_code=204)
def delete_key(key_id: str, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    k = db.query(ApiKey).filter(ApiKey.id == key_id, ApiKey.user_id == user.id).first()
    if not k:
        raise HTTPException(status_code=404, detail="Ключ не найден")
    db.delete(k)
    db.commit()
    return None