import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.audit import write_audit
from app.database import get_db
from app.models.scenario import Scenario, ScenarioStatus, ScenarioVersion
from app.models.user import User
from app.schemas.scenario import (
    ScenarioCreate,
    ScenarioOut,
    ScenarioUpdate,
    ScenarioVersionOut,
    ValidationOut,
)
from app.services.validator import check_registration_intent, invalidate_validation_cache, validate_scenario

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])


def _to_out(s: Scenario) -> ScenarioOut:
    tags = [t for t in s.tags.split(",") if t] if s.tags else []
    return ScenarioOut(
        id=s.id, name=s.name, url=s.url,
        mode=s.mode.value if hasattr(s.mode, "value") else s.mode,
        script_text=s.script_text,
        status=s.status.value if hasattr(s.status, "value") else s.status,
        tags=tags, current_version=s.current_version,
        created_at=s.created_at, updated_at=s.updated_at,
    )


def _snapshot(s: Scenario) -> str:
    return json.dumps({
        "name": s.name, "url": s.url, "script_text": s.script_text,
        "description": s.description,
        "mode": s.mode.value if hasattr(s.mode, "value") else s.mode,
        "tags": s.tags,
    }, ensure_ascii=False)


@router.post("/validate", response_model=ValidationOut)
def validate(body: dict, user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    text = (body.get("script_text") or "").strip()
    if not text:
        return ValidationOut(valid=False, errors=["Введите текст сценария"])
    if check_registration_intent(text):
        return ValidationOut(
            valid=False,
            errors=["Функция автоматической регистрации будет доступна в следующей версии. Для тестирования авторизации, пожалуйста, введите готовые учётные данные в сценарий вручную."],
            suggestions=["Введите в сценарий явные команды ввода логина и пароля с готовыми учётными данными."],
        )
    result = validate_scenario(text)
    return ValidationOut(**result)


@router.post("", response_model=ScenarioOut, status_code=201)
def create_scenario(body: ScenarioCreate, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db), request: Request = None):
    if not body.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL должен начинаться с http:// или https://")
    scenario = Scenario(
        owner_id=user.id,
        name=body.name,
        url=body.url,
        mode=body.mode,
        script_text=body.script_text,
        description=body.description,
        tags=",".join(body.tags),
        status=ScenarioStatus.draft,
        current_version=1,
        template_key=body.template_key,
    )
    db.add(scenario)
    db.commit()
    db.refresh(scenario)
    db.add(ScenarioVersion(
        scenario_id=scenario.id, version=1, snapshot=_snapshot(scenario),
        author_id=user.id, change_comment="Создание сценария",
    ))
    db.commit()
    write_audit(db, user.id, "scenario_create", "scenario", scenario.id, request)
    return _to_out(scenario)


@router.get("", response_model=list[ScenarioOut])
def list_scenarios(tag: str | None = Query(default=None),
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Scenario).filter(Scenario.owner_id == user.id)
    scenarios = q.order_by(Scenario.updated_at.desc()).all()
    if tag:
        tag_l = tag.lower()
        scenarios = [s for s in scenarios
                     if any(t.lower() == tag_l for t in (s.tags or "").split(",") if t.strip())]
    return [_to_out(s) for s in scenarios]


@router.get("/{scenario_id}", response_model=ScenarioOut)
def get_scenario(scenario_id: str, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    s = db.query(Scenario).filter(Scenario.id == scenario_id, Scenario.owner_id == user.id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Сценарий не найден")
    return _to_out(s)


@router.put("/{scenario_id}", response_model=ScenarioOut)
def update_scenario(scenario_id: str, body: ScenarioUpdate,
                    user: User = Depends(get_current_user),
                    db: Session = Depends(get_db), request: Request = None):
    s = db.query(Scenario).filter(Scenario.id == scenario_id, Scenario.owner_id == user.id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Сценарий не найден")

    old_snapshot = _snapshot(s)
    if body.name is not None:
        s.name = body.name
    if body.url is not None:
        if not body.url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="URL должен начинаться с http:// или https://")
        s.url = body.url
    if body.script_text is not None:
        s.script_text = body.script_text
    if body.description is not None:
        s.description = body.description
    if body.tags is not None:
        s.tags = ",".join(body.tags)
    if body.status is not None:
        if body.status == "deprecated" and not body.change_comment:
            raise HTTPException(status_code=400, detail="Комментарий к изменению обязателен при переводе в Deprecated")
        try:
            s.status = ScenarioStatus(body.status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Недопустимый статус: {body.status}")

    # only bump version if content changed
    if old_snapshot != _snapshot(s):
        s.current_version += 1
        db.add(ScenarioVersion(
            scenario_id=s.id, version=s.current_version, snapshot=_snapshot(s),
            author_id=user.id, change_comment=body.change_comment or "Обновление сценария",
        ))
        invalidate_validation_cache()

    db.commit()
    db.refresh(s)
    write_audit(db, user.id, "scenario_update", "scenario", s.id, request,
                meta={"version": s.current_version})
    return _to_out(s)


@router.delete("/{scenario_id}", status_code=204)
def delete_scenario(scenario_id: str, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db), request: Request = None):
    s = db.query(Scenario).filter(Scenario.id == scenario_id, Scenario.owner_id == user.id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Сценарий не найден")
    write_audit(db, user.id, "scenario_delete", "scenario", s.id, request)
    db.delete(s)
    db.commit()
    return None


@router.post("/{scenario_id}/rollback", response_model=ScenarioOut)
def rollback_scenario(scenario_id: str, version: int,
                      user: User = Depends(get_current_user),
                      db: Session = Depends(get_db), request: Request = None):
    s = db.query(Scenario).filter(Scenario.id == scenario_id, Scenario.owner_id == user.id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Сценарий не найден")
    ver = db.query(ScenarioVersion).filter(
        ScenarioVersion.scenario_id == scenario_id,
        ScenarioVersion.version == version,
    ).first()
    if not ver:
        raise HTTPException(status_code=404, detail="Версия не найдена")
    data = json.loads(ver.snapshot)
    s.name = data.get("name", s.name)
    s.url = data.get("url", s.url)
    s.script_text = data.get("script_text", s.script_text)
    s.description = data.get("description", s.description)
    s.mode = data.get("mode", s.mode.value if hasattr(s.mode, "value") else s.mode)
    s.tags = data.get("tags", s.tags)
    if data.get("status"):
        s.status = data["status"]
    s.current_version += 1
    db.add(ScenarioVersion(
        scenario_id=s.id, version=s.current_version, snapshot=_snapshot(s),
        author_id=user.id, change_comment=f"Откат к версии {version}",
    ))
    db.commit()
    db.refresh(s)
    write_audit(db, user.id, "scenario_update", "scenario", s.id, request,
                meta={"rollback_to": version})
    return _to_out(s)


@router.get("/{scenario_id}/versions", response_model=list[ScenarioVersionOut])
def list_versions(scenario_id: str, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    s = db.query(Scenario).filter(Scenario.id == scenario_id, Scenario.owner_id == user.id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Сценарий не найден")
    versions = db.query(ScenarioVersion).filter(
        ScenarioVersion.scenario_id == scenario_id
    ).order_by(ScenarioVersion.version.desc()).all()
    return [ScenarioVersionOut(
        id=v.id, version=v.version, snapshot=v.snapshot, author_id=v.author_id,
        change_comment=v.change_comment, created_at=v.created_at,
    ) for v in versions]