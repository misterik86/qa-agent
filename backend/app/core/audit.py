import json
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.config import settings
from app.models.audit import AuditLog, AuditEvent


def write_audit(
    db: Session,
    user_id: str | None,
    event: AuditEvent | str,
    entity_type: str = "",
    entity_id: str | None = None,
    request: Request | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    if not settings.AUDIT_ENABLED:
        return
    ip = ""
    ua = ""
    if request:
        ip = request.client.host if request.client else ""
        ua = request.headers.get("user-agent", "")[:255]
    log = AuditLog(
        user_id=user_id,
        event=event if isinstance(event, str) else event.value,
        entity_type=entity_type,
        entity_id=entity_id,
        ip=ip,
        user_agent=ua,
        meta=json.dumps(meta or {}, ensure_ascii=False),
    )
    db.add(log)
    db.commit()