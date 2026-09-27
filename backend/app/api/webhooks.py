from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.models.webhook import WebhookConfig, WebhookStatus
from app.schemas.misc import WebhookCreate, WebhookOut

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _to_out(w: WebhookConfig) -> WebhookOut:
    return WebhookOut(
        id=w.id, url=w.url,
        event=w.event.value if hasattr(w.event, "value") else w.event,
        status=w.status.value if hasattr(w.status, "value") else w.status,
        created_at=w.created_at,
    )


@router.post("", response_model=WebhookOut, status_code=201)
def create_webhook(body: WebhookCreate, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    if not body.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL вебхука должен начинаться с http:// или https://")
    wh = WebhookConfig(owner_id=user.id, url=body.url, event=body.event, secret=body.secret)
    db.add(wh)
    db.commit()
    db.refresh(wh)
    return _to_out(wh)


@router.get("", response_model=list[WebhookOut])
def list_webhooks(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(WebhookConfig).filter(WebhookConfig.owner_id == user.id).all()
    return [_to_out(w) for w in rows]


@router.delete("/{webhook_id}", status_code=204)
def delete_webhook(webhook_id: str, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    wh = db.query(WebhookConfig).filter(WebhookConfig.id == webhook_id,
                                        WebhookConfig.owner_id == user.id).first()
    if not wh:
        raise HTTPException(status_code=404, detail="Вебхук не найден")
    db.delete(wh)
    db.commit()
    return None