"""Public (share-by-link) report endpoints — no authentication, access by secret token."""
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.test_run import TestRun, TestStep
from app.services.reports import build_report_data

router = APIRouter(prefix="/api/public", tags=["public"])


def _get_run_by_token(db: Session, token: str) -> TestRun:
    run = db.query(TestRun).filter(TestRun.share_token == token).first()
    if not run:
        raise HTTPException(status_code=404, detail="Отчёт не найден или ссылка отозвана")
    return run


@router.get("/reports/{token}")
def public_report(token: str, db: Session = Depends(get_db)):
    run = _get_run_by_token(db, token)
    data = build_report_data(run)
    from app.services.humanize import humanize_step

    result_steps = {s.get("order"): s for s in (run.result_json or {}).get("steps", []) if s.get("order")}
    # Public payload: no user info, only site-facing results. Secrets masked.
    return {
        "name": data["name"],
        "url": data["url"],
        "mode": data["mode"],
        "status": data["status"],
        "status_label": data["status_label"],
        "total_time_seconds": data["total_time_seconds"],
        "total_steps": data["total_steps"],
        "passed_steps": data["passed_steps"],
        "failed_steps": data["failed_steps"],
        "console_errors": data["console_errors"][:20],
        "error_message": data["error_message"],
        "finished_at": data["finished_at"],
        "steps": [{
            "order": s["order"], "action": s["action"],
            "action_text": humanize_step(
                s["action"], s.get("selector", ""), s.get("value", ""),
                result_steps.get(s["order"], {}).get("expected_effect", ""),
                form_data=result_steps.get(s["order"], {}).get("form_data"),
                mask=True),
            "expected": s["expected"], "status": s["status"], "message": s["message"],
            "console_error": s["console_error"], "page": result_steps.get(s["order"], {}).get("page", ""),
            "has_screenshot": bool(s.get("screenshot")),
        } for s in data["steps"]],
    }


@router.get("/reports/{token}/screenshot/{order}")
def public_screenshot(token: str, order: int, db: Session = Depends(get_db)):
    run = _get_run_by_token(db, token)
    step = db.query(TestStep).filter(
        TestStep.test_run_id == run.id,
        TestStep.order == order,
        TestStep.screenshot_path.isnot(None),
    ).first()
    if not step or not os.path.exists(step.screenshot_path):
        raise HTTPException(status_code=404, detail="Скриншот не найден")
    with open(step.screenshot_path, "rb") as fh:
        return Response(content=fh.read(), media_type="image/png")
