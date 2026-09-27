"""TestRail / Xray integration for Enterprise (export test results)."""
import logging

import httpx

from app.config import settings
from app.models.test_run import TestRun
from app.services.reports import build_report_data

logger = logging.getLogger(__name__)


def export_to_testrail(run: TestRun, case_id: str | None = None,
                       run_id: str | None = None, mapping: dict | None = None) -> dict:
    """Push a test result into TestRail via API."""
    if not settings.TESTRAIL_URL:
        return {"ok": False, "error": "TestRail не настроен"}
    data = build_report_data(run)
    status_id = 1 if data["status"] == "passed" else 5  # 1 passed, 5 failed
    payload = {
        "status_id": status_id,
        "comment": data.get("error_message") or f"QA Agent: {data['passed_steps']}P/{data['failed_steps']}F",
        "elapsed": f"{max(1, int(data['total_time_seconds']))}s",
    }
    if not run_id or not case_id:
        return {"ok": False, "error": "TestRail: укажите run_id и case_id для экспорта"}
    target = run_id
    url = f"{settings.TESTRAIL_URL}/index.php?/api/v2/add_result_for_case/{target}/{case_id}"
    try:
        with httpx.Client(timeout=20, auth=(settings.TESTRAIL_USER, settings.TESTRAIL_PASSWORD)) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            return {"ok": True, "response": resp.json()}
    except Exception as exc:
        logger.error("TestRail export failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def export_to_xray(run: TestRun, test_key: str | None = None, mapping: dict | None = None) -> dict:
    """Push test result to Xray (Jira Cloud) via import execution results API."""
    if not settings.TESTRAIL_URL:
        return {"ok": False, "error": "Xray не настроен"}
    data = build_report_data(run)
    status = "PASSED" if data["status"] == "passed" else "FAILED"
    payload = {
        "testExecutionKey": "QAAGENT-EXEC",
        "tests": [{
            "testKey": test_key or "QAAGENT-1",
            "status": status,
            "comment": data.get("error_message") or "QA Agent результат",
        }],
    }
    url = f"{settings.TESTRAIL_URL}/api/v1/import/execution"
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            return {"ok": True, "response": resp.json()}
    except Exception as exc:
        logger.error("Xray export failed: %s", exc)
        return {"ok": False, "error": str(exc)}