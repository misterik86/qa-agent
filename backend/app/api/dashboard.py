from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.scenario import Scenario
from app.models.test_run import RunStatus, TestRun
from app.models.user import User
from app.services.limits import get_parallel_limit, monthly_used, usage_percent, plan_of

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
def dashboard_stats(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    total_runs = db.query(TestRun).filter(TestRun.owner_id == user.id).count()
    passed = db.query(TestRun).filter(TestRun.owner_id == user.id,
                                      TestRun.status == RunStatus.passed.value).count()
    failed = db.query(TestRun).filter(TestRun.owner_id == user.id,
                                      TestRun.status == RunStatus.failed.value).count()
    scenario_count = db.query(Scenario).filter(Scenario.owner_id == user.id).count()
    running = db.query(TestRun).filter(TestRun.owner_id == user.id,
                                       TestRun.status.in_([RunStatus.running.value,
                                                           RunStatus.queued.value])).count()
    recent_runs = db.query(TestRun).filter(TestRun.owner_id == user.id) \
        .order_by(TestRun.created_at.desc()).limit(5).all()

    return {
        "total_runs": total_runs,
        "passed": passed,
        "failed": failed,
        "scenarios": scenario_count,
        "running_now": running,
        "plan": plan_of(user),
        "usage_percent": usage_percent(user),
        "tests_used": monthly_used(user),
        "parallel_limit": get_parallel_limit(user),
        "recent_runs": [{
            "id": r.id, "name": r.name, "status": r.status.value, "url": r.url,
            "created_at": r.created_at, "passed_steps": r.passed_steps,
            "failed_steps": r.failed_steps,
        } for r in recent_runs],
    }