"""Celery task that executes a test run and persists results."""
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from app.database import SessionLocal
from app.models.test_run import RunStatus, TestRun, TestStep
from app.services.limits import register_test_use
from app.services.reports import build_report_data
from app.services.time_prediction import predict_duration, save_prediction
from app.services.webhooks import notify_test_finished
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.run_test.run_test_task", bind=True, track_started=True, max_retries=0)
def run_test_task(self, test_run_id: str, user_id: str, url: str, mode: str,
                  script_text: str, name: str) -> dict:
    from app.agent.runner import run_full_page, run_scenario, run_site_crawl, run_steps

    db = SessionLocal()
    run = db.query(TestRun).filter(TestRun.id == test_run_id).first()
    if not run:
        db.close()
        return {"status": "error"}

    run.status = RunStatus.running
    run.started_at = datetime.now(timezone.utc)
    db.commit()

    # Optional site credentials (run-level wins over suite-level binding).
    from app.core.crypto import decrypt_secret
    from app.models.suite import SiteCredential, TestCase, TestSuite

    suite = db.query(TestSuite).filter(TestSuite.id == run.suite_id).first() if run.suite_id else None
    cred_id = run.credential_id or (suite.credential_id if suite else None)
    login_cfg = None
    if cred_id:
        cred = db.query(SiteCredential).filter(SiteCredential.id == cred_id).first()
        if cred and cred.username:
            login_cfg = {
                "label": cred.label,
                "login_url": cred.login_url or "",
                "username": cred.username,
                "password": decrypt_secret(cred.password_encrypted),
                "username_selector": cred.username_selector,
                "password_selector": cred.password_selector,
                "submit_selector": cred.submit_selector,
            }

    # Suite mode: execute the persistent curated set of cases.
    case_ids: list[str] = []
    if run.suite_id:
        cases = (db.query(TestCase)
                 .filter(TestCase.suite_id == run.suite_id, TestCase.enabled == True)  # noqa: E712
                 .order_by(TestCase.order).all())
        steps = [c.to_step() for c in cases]
        case_ids = [c.id for c in cases]
        try:
            result = run_steps(url=url, steps=steps, test_run_id=test_run_id,
                               name=name, login=login_cfg)
        except Exception as exc:
            logger.exception("suite run %s crashed", test_run_id)
            result = {"status": "error", "error": str(exc), "steps": [], "console_errors": []}
    else:
        try:
            if mode == "site":
                result = run_site_crawl(url=url, test_run_id=test_run_id, name=name,
                                        login=login_cfg)
            elif mode == "full_page":
                result = run_full_page(url=url, test_run_id=test_run_id, name=name,
                                       login=login_cfg)
            else:
                result = run_scenario(url=url, script_text=script_text, mode=mode,
                                      test_run_id=test_run_id, name=name, login=login_cfg)
        except Exception as exc:
            logger.exception("run %s crashed", test_run_id)
            result = {"status": "error", "error": str(exc), "steps": [], "console_errors": []}

    db.expire_all()
    run = db.query(TestRun).filter(TestRun.id == test_run_id).first()

    status_map = {
        "passed": RunStatus.passed,
        "failed": RunStatus.failed,
        "timeout": RunStatus.timeout,
        "error": RunStatus.error,
        "cancelled": RunStatus.cancelled,
    }
    final_status = status_map.get(result.get("status"), RunStatus.failed)
    run.status = final_status
    run.finished_at = datetime.now(timezone.utc)
    run.error_message = result.get("error", "")
    run.console_errors = "\n".join(result.get("console_errors", []))
    run.result_json = result

    # Persist steps
    screenshots_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "screenshots")
    os.makedirs(screenshots_dir, exist_ok=True)
    cursor = run.started_at or datetime.now(timezone.utc)
    for s in result.get("steps", []):
        screenshot_path = None
        raw_shot = s.get("screenshot") or s.get("screenshot_path")
        if raw_shot:
            if raw_shot.startswith("data:image/png;base64,"):
                import base64

                fname = f"{test_run_id}_{s.get('order', 0)}.png"
                fpath = os.path.join(screenshots_dir, fname)
                try:
                    with open(fpath, "wb") as fh:
                        fh.write(base64.b64decode(raw_shot.split(",", 1)[1]))
                    screenshot_path = fpath
                except Exception:
                    screenshot_path = None
            else:
                screenshot_path = raw_shot
        dur = float(s.get("duration_seconds") or 0.0)
        started = cursor
        finished = cursor + timedelta(seconds=dur)
        cursor = finished
        db.add(TestStep(
            test_run_id=test_run_id,
            order=s.get("order", 0),
            action=s.get("action", ""),
            selector=s.get("selector", ""),
            value=s.get("value", ""),
            expected=s.get("expected", ""),
            test_case_id=case_ids[s.get("order", 1) - 1]
            if case_ids and 0 <= s.get("order", 1) - 1 < len(case_ids) else None,
            status="passed" if s.get("status") == "passed" else "failed",
            message=s.get("message", ""),
            console_error=s.get("console_error", ""),
            screenshot_path=screenshot_path,
            started_at=started,
            finished_at=finished,
        ))
    run.total_steps = len(result.get("steps", []))
    run.passed_steps = sum(1 for s in result.get("steps", []) if s.get("status") == "passed")
    run.failed_steps = run.total_steps - run.passed_steps

    db.commit()

    # Register usage for quota
    try:
        register_test_use(db, run.owner)
    except Exception:
        db.rollback()

    # Save time prediction actual
    try:
        num_steps = run.total_steps
        num_elements = len(result.get("elements", []))
        predicted = predict_duration(db, user_id, num_steps, num_elements)
        save_prediction(db, test_run_id, predicted=predicted,
                        actual=int(run.duration_seconds or 0),
                        num_steps=num_steps, num_elements=num_elements)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("prediction save failed")

    # Notify webhooks
    try:
        notify_test_finished(db, user_id, test_run_id, build_report_data(run))
    except Exception:
        logger.exception("webhook notify failed")

    # Optional TestRail/Xray export for enterprise
    db.expire_all()
    run = db.query(TestRun).filter(TestRun.id == test_run_id).first()
    if run and run.owner and getattr(run.owner, "plan", "") in ("enterprise",):
        try:
            from app.config import settings
            from app.services.testrail import export_to_testrail

            result = export_to_testrail(
                run,
                run_id=getattr(settings, "TESTRAIL_PROJECT_ID", None),
                case_id=None,
            )
            if not result.get("ok"):
                logger.warning("testrail export skipped: %s", result.get("error"))
        except Exception:
            logger.exception("testrail export failed")

    db.close()
    return {"status": final_status.value if hasattr(final_status, "value") else final_status}