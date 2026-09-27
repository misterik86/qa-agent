"""Site credentials for authenticated-area testing. Passwords are Fernet-encrypted
and never returned by the API."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.crypto import encrypt_secret
from app.database import get_db
from app.models.suite import SiteCredential, TestSuite
from app.models.user import User

router = APIRouter(prefix="/api/credentials", tags=["credentials"])


class CredentialCreate(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    login_url: str = ""
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=255)
    username_selector: str = ""
    password_selector: str = ""
    submit_selector: str = ""


class CredentialPatch(BaseModel):
    label: str | None = Field(default=None, max_length=100)
    login_url: str | None = None
    username: str | None = None
    password: str | None = None  # empty/None = keep old
    username_selector: str | None = None
    password_selector: str | None = None
    submit_selector: str | None = None


class CredentialOut(BaseModel):
    id: str
    label: str
    login_url: str
    username: str
    username_selector: str
    password_selector: str
    submit_selector: str
    used_by_suites: int = 0
    created_at: object | None = None

    class Config:
        from_attributes = True


def _out(db: Session, c: SiteCredential) -> CredentialOut:
    return CredentialOut(
        id=c.id, label=c.label, login_url=c.login_url, username=c.username,
        username_selector=c.username_selector, password_selector=c.password_selector,
        submit_selector=c.submit_selector,
        used_by_suites=db.query(TestSuite).filter(TestSuite.credential_id == c.id).count(),
        created_at=c.created_at,
    )


def _get(db: Session, cred_id: str, user: User) -> SiteCredential:
    cred = db.query(SiteCredential).filter(
        SiteCredential.id == cred_id, SiteCredential.owner_id == user.id).first()
    if not cred:
        raise HTTPException(status_code=404, detail="Учётные данные не найдены")
    return cred


@router.post("", response_model=CredentialOut, status_code=201)
def create(body: CredentialCreate, user: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    if body.login_url and not body.login_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL входа должен начинаться с http:// или https://")
    cred = SiteCredential(
        owner_id=user.id, label=body.label, login_url=body.login_url,
        username=body.username, password_encrypted=encrypt_secret(body.password),
        username_selector=body.username_selector, password_selector=body.password_selector,
        submit_selector=body.submit_selector,
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)
    return _out(db, cred)


@router.get("", response_model=list[CredentialOut])
def list_all(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    creds = db.query(SiteCredential).filter(SiteCredential.owner_id == user.id)\
        .order_by(SiteCredential.created_at.desc()).all()
    return [_out(db, c) for c in creds]


@router.patch("/{cred_id}", response_model=CredentialOut)
def patch(cred_id: str, body: CredentialPatch, user: User = Depends(get_current_user),
          db: Session = Depends(get_db)):
    cred = _get(db, cred_id, user)
    for field in ("label", "login_url", "username", "username_selector",
                  "password_selector", "submit_selector"):
        val = getattr(body, field)
        if val is not None:
            setattr(cred, field, val)
    if body.password:
        cred.password_encrypted = encrypt_secret(body.password)
    db.commit()
    db.refresh(cred)
    return _out(db, cred)


@router.delete("/{cred_id}", status_code=204)
def delete(cred_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cred = _get(db, cred_id, user)
    suites_using = db.query(TestSuite).filter(TestSuite.credential_id == cred.id).count()
    if suites_using:
        raise HTTPException(
            status_code=409,
            detail=f"Учётные данные используются в {suites_using} набор(ах). Сначала отвяжите их.")
    db.delete(cred)
    db.commit()
