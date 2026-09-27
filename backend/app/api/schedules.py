from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.schedule import ScheduleJob, ScheduleRunLog, ScheduleStatus
from app.models.user import User
from app.schemas.misc import ScheduleCreate, ScheduleOut

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


def _to_out(j: ScheduleJob) -> ScheduleOut:
    return ScheduleOut(
        id=j.id, scenario_id=j.scenario_id,
        frequency=j.frequency.value if hasattr(j.frequency, "value") else j.frequency,
        channel=j.channel.value if hasattr(j.channel, "value") else j.channel,
        chat_id=j.chat_id, vk_user_id=j.vk_user_id, email_recipient=j.email_recipient,
        status=j.status.value if hasattr(j.status, "value") else j.status,
        last_run_at=j.last_run_at, created_at=j.created_at,
    )


@router.post("", response_model=ScheduleOut, status_code=201)
def create_schedule(body: ScheduleCreate, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    from app.models.scenario import Scenario

    scenario = db.query(Scenario).filter(Scenario.id == body.scenario_id,
                                         Scenario.owner_id == user.id).first()
    if not scenario:
        raise HTTPException(status_code=404, detail="Сценарий не найден")
    job = ScheduleJob(
        owner_id=user.id,
        scenario_id=body.scenario_id,
        frequency=body.frequency,
        channel=body.channel,
        chat_id=body.chat_id,
        vk_user_id=body.vk_user_id,
        vk_token_encrypted=__import__("app.core.crypto", fromlist=["encrypt_secret"]).encrypt_secret(body.vk_token) if body.vk_token else None,
        email_recipient=body.email_recipient,
        status=ScheduleStatus.active,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return _to_out(job)


@router.get("", response_model=list[ScheduleOut])
def list_schedules(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    jobs = db.query(ScheduleJob).filter(ScheduleJob.owner_id == user.id).order_by(ScheduleJob.created_at.desc()).all()
    return [_to_out(j) for j in jobs]


@router.post("/{schedule_id}/pause")
def pause_schedule(schedule_id: str, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    job = db.query(ScheduleJob).filter(ScheduleJob.id == schedule_id,
                                       ScheduleJob.owner_id == user.id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Расписание не найдено")
    job.status = ScheduleStatus.paused
    db.commit()
    return {"message": "Расписание приостановлено"}


@router.post("/{schedule_id}/resume")
def resume_schedule(schedule_id: str, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    job = db.query(ScheduleJob).filter(ScheduleJob.id == schedule_id,
                                       ScheduleJob.owner_id == user.id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Расписание не найдено")
    job.status = ScheduleStatus.active
    db.commit()
    return {"message": "Расписание возобновлено"}


@router.delete("/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: str, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    job = db.query(ScheduleJob).filter(ScheduleJob.id == schedule_id,
                                       ScheduleJob.owner_id == user.id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Расписание не найдено")
    db.delete(job)
    db.commit()
    return None