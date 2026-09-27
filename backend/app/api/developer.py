from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import authenticate_api_key
from app.database import get_db
from app.models.test_run import TestRun
from app.models.user import User
from app.schemas.run import TestRunCreate, TestRunOut
from app.services.run_creator import create_run_checked

router = APIRouter(prefix="/api/v1/tests", tags=["developer-api"])


@router.post("/run", response_model=TestRunOut, status_code=202)
def run_test_via_api(body: TestRunCreate, user: User = Depends(authenticate_api_key),
                     db: Session = Depends(get_db)):
    if not body.consent:
        raise HTTPException(status_code=400, detail="Необходимо подтвердить: «Я имею право тестировать этот сайт»")
    url = body.url
    mode = body.mode
    name = body.name
    script_text = body.script_text
    suite_id = body.suite_id
    if body.scenario_id:
        from app.models.scenario import Scenario

        sc = db.query(Scenario).filter(Scenario.id == body.scenario_id,
                                       Scenario.owner_id == user.id).first()
        if not sc:
            raise HTTPException(status_code=404, detail="Сценарий не найден")
        name = sc.name
        url = sc.url
        mode = sc.mode.value if hasattr(sc.mode, "value") else sc.mode
        script_text = sc.script_text
    elif suite_id:
        from app.models.suite import TestSuite

        suite = db.query(TestSuite).filter(TestSuite.id == suite_id,
                                           TestSuite.owner_id == user.id).first()
        if not suite:
            raise HTTPException(status_code=404, detail="Набор не найден")
        name = name if body.name != "Без названия" else suite.name
        url = suite.url
        mode = suite.mode if isinstance(suite.mode, str) else suite.mode.value

    run = create_run_checked(db, user, None, name=name, url=url, mode=mode,
                             script_text=script_text, scenario_id=body.scenario_id,
                             suite_id=suite_id)
    return TestRunOut(
        id=run.id, scenario_id=run.scenario_id, suite_id=run.suite_id, name=run.name, url=run.url,
        mode=run.mode, status=run.status.value, total_steps=0, passed_steps=0, failed_steps=0,
        console_errors=[], error_message="", duration_seconds=0.0,
        started_at=run.started_at, finished_at=run.finished_at, created_at=run.created_at,
    )


@router.get("/runs/{run_id}/summary")
def run_summary(run_id: str, user: User = Depends(authenticate_api_key),
                db: Session = Depends(get_db)):
    """Compact status for CI polling: {status, passed_steps, total_steps}."""
    run = db.query(TestRun).filter(TestRun.id == run_id, TestRun.owner_id == user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Прогон не найден")
    status = run.status.value if hasattr(run.status, "value") else str(run.status)
    finished = status in ("passed", "failed", "cancelled", "timeout", "error", "limit_exceeded")
    return {
        "id": run.id,
        "status": status,
        "finished": finished,
        "total_steps": run.total_steps,
        "passed_steps": run.passed_steps,
        "failed_steps": run.failed_steps,
        "url": run.url,
        "duration_seconds": round(run.duration_seconds, 2),
    }
