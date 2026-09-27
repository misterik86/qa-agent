import os
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.scenario import Scenario
from app.models.suite import TestCase, TestSuite
from app.models.test_run import RunStatus, TestRun, TestStep
from app.models.user import User
from app.schemas.run import TestRunCreate, TestRunDetail, TestRunOut
from app.schemas.suite import SaveAsSuiteRequest, SuiteOut
from app.services.reports import export_report, build_report_data
from app.services.run_creator import create_run_checked

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _to_out(run: TestRun) -> TestRunOut:
    return TestRunOut(
        id=run.id, scenario_id=run.scenario_id, suite_id=run.suite_id,
        name=run.name, url=run.url,
        mode=run.mode.value if hasattr(run.mode, "value") else run.mode,
        status=run.status.value if hasattr(run.status, "value") else run.status,
        total_steps=run.total_steps, passed_steps=run.passed_steps, failed_steps=run.failed_steps,
        console_errors=[e for e in (run.console_errors or "").split("\n") if e.strip()],
        error_message=run.error_message, duration_seconds=round(run.duration_seconds, 2),
        share_token=run.share_token,
        started_at=run.started_at, finished_at=run.finished_at, created_at=run.created_at,
    )


def _case_title(step: dict) -> str:
    return step.get("expected") or f"{step.get('action', '')} {step.get('selector', '')}".strip()


def _compute_delta(db: Session, run: TestRun) -> tuple[dict | None, dict[str, str]]:
    """Compare per-case statuses with the previous finished run of the same suite."""
    if not run.suite_id:
        return None, {}
    prev = (db.query(TestRun)
            .filter(TestRun.suite_id == run.suite_id,
                    TestRun.id != run.id,
                    TestRun.created_at < run.created_at,
                    TestRun.status.in_(["passed", "failed"]))
            .order_by(TestRun.created_at.desc())
            .first())
    if not prev:
        return None, {}

    def _statuses(r: TestRun) -> dict[str, str]:
        rows = (db.query(TestStep.test_case_id, TestStep.status)
                .filter(TestStep.test_run_id == r.id, TestStep.test_case_id.isnot(None)).all())
        return {cid: st for cid, st in rows}

    cur = _statuses(run)
    old = _statuses(prev)
    regressed, fixed, new_cases = [], [], 0
    step_delta: dict[str, str] = {}
    for cid, status in cur.items():
        prev_status = old.get(cid)
        if prev_status is None:
            new_cases += 1
            if status == "failed":
                step_delta[cid] = "new"
            continue
        if prev_status == "passed" and status == "failed":
            regressed.append(cid)
            step_delta[cid] = "regressed"
        elif prev_status == "failed" and status == "passed":
            fixed.append(cid)
            step_delta[cid] = "fixed"

    titles = {}
    if regressed or fixed or new_cases:
        rows = (db.query(TestStep).filter(TestStep.test_run_id == run.id).all())
        titles = {s.test_case_id: _case_title({
            "expected": s.expected, "action": s.action, "selector": s.selector}) for s in rows}
    delta = {
        "prev_run_id": prev.id,
        "regressed": [titles.get(c, c) for c in regressed],
        "fixed": [titles.get(c, c) for c in fixed],
        "new_cases": new_cases,
    }
    parts = []
    if regressed:
        parts.append(f"новых провалов: {len(regressed)}")
    if fixed:
        parts.append(f"починено: {len(fixed)}")
    if new_cases:
        parts.append(f"новых кейсов: {new_cases}")
    delta["summary"] = "; ".join(parts) if parts else "изменений нет — результаты как в прошлом прогоне"
    return delta, step_delta


def _detail(run: TestRun, db: Session) -> TestRunDetail:
    data = build_report_data(run)
    delta, step_delta = _compute_delta(db, run)
    from app.services.humanize import humanize_step, strip_selectors

    result_steps = (run.result_json or {}).get("steps", []) or []
    steps = []
    for i, s in enumerate(data["steps"], 1):
        raw = result_steps[i - 1] if i - 1 < len(result_steps) else {}
        action_text = humanize_step(
            s["action"], s.get("selector", ""), s.get("value", ""),
            raw.get("expected_effect", ""), form_data=raw.get("form_data"))
        steps.append({
            "order": i, "action": s["action"], "action_text": action_text,
            "selector": s["selector"],
            "value": s["value"], "expected": s["expected"], "status": s["status"],
            "message": strip_selectors(s["message"]), "console_error": s["console_error"],
            "screenshot": s["screenshot"], "page": raw.get("page", ""),
            "delta": step_delta.get(s.get("test_case_id", ""), "") if step_delta else "",
        })
    return TestRunDetail(
        id=run.id, name=run.name, url=run.url,
        mode=data["mode"], status=data["status"], status_label=data["status_label"],
        total_time_seconds=data["total_time_seconds"], total_steps=data["total_steps"],
        passed_steps=data["passed_steps"], failed_steps=data["failed_steps"],
        console_errors=data["console_errors"], error_message=data["error_message"],
        steps=steps, suite_id=run.suite_id, share_token=run.share_token,
        delta=delta, started_at=run.started_at, finished_at=run.finished_at,
    )


@router.post("", response_model=TestRunOut, status_code=201)
def create_run(body: TestRunCreate, user: User = Depends(get_current_user),
               db: Session = Depends(get_db), request: Request = None):
    if not body.consent:
        raise HTTPException(status_code=400, detail="Необходимо подтвердить: «Я имею право тестировать этот сайт»")

    # Resolve scenario first: the effective URL may come from the saved scenario.
    scenario_id = body.scenario_id
    name = body.name
    url = body.url
    mode = body.mode
    script_text = body.script_text
    if scenario_id:
        sc = db.query(Scenario).filter(Scenario.id == scenario_id, Scenario.owner_id == user.id).first()
        if not sc:
            raise HTTPException(status_code=404, detail="Сценарий не найден")
        name = sc.name
        url = sc.url
        mode = sc.mode.value if hasattr(sc.mode, "value") else sc.mode
        script_text = sc.script_text

    run = create_run_checked(db, user, request, name=name, url=url, mode=mode,
                             script_text=script_text, scenario_id=scenario_id,
                             suite_id=body.suite_id, credential_id=body.credential_id)

    out = _to_out(run)
    from app.services.time_prediction import predict_duration

    out.predicted_seconds = predict_duration(db, user.id, max(1, run.total_steps or 1))
    return out


@router.post("/{run_id}/save-as-suite", response_model=SuiteOut, status_code=201)
def save_as_suite(run_id: str, body: SaveAsSuiteRequest,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Persist the generated steps of a finished scan-run as a curated regression suite."""
    run = db.query(TestRun).filter(TestRun.id == run_id, TestRun.owner_id == user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Прогон не найден")
    steps = [s for s in (run.result_json or {}).get("steps", [])
             if s.get("action") and s.get("action") != "scan"]
    # dedup identical action+selector+value+expected pairs (e.g. repeated validation cases)
    seen = set()
    unique_steps = []
    for s in steps:
        key = (s.get("action"), s.get("selector"), s.get("value"), s.get("expected"))
        if key not in seen:
            seen.add(key)
            unique_steps.append(s)
    if not unique_steps:
        raise HTTPException(status_code=400, detail="В прогоне нет шагов, которые можно сохранить как набор")

    suite = TestSuite(owner_id=user.id, name=body.name, url=run.url,
                      mode=run.mode.value if hasattr(run.mode, "value") else run.mode)
    db.add(suite)
    db.flush()
    for idx, s in enumerate(unique_steps):
        db.add(TestCase(
            suite_id=suite.id, order=idx,
            action=s.get("action", ""), selector=(s.get("selector") or "")[:512],
            value=s.get("value", ""), expected=s.get("expected", ""),
            expected_effect=s.get("expected_effect", ""),
            enabled=True, source="auto",
        ))
    db.commit()
    db.refresh(suite)

    def _suite_out_local(s: TestSuite) -> SuiteOut:
        return SuiteOut(id=s.id, name=s.name, url=s.url,
                        mode=s.mode if isinstance(s.mode, str) else s.mode.value,
                        credential_id=s.credential_id,
                        cases_count=len(unique_steps), enabled_cases_count=len(unique_steps),
                        created_at=s.created_at)
    return _suite_out_local(suite)


@router.post("/{run_id}/share")
def share_run(run_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    run = db.query(TestRun).filter(TestRun.id == run_id, TestRun.owner_id == user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Прогон не найден")
    if not run.share_token:
        run.share_token = secrets.token_urlsafe(24)
        db.commit()
    return {"token": run.share_token, "url": f"/report/{run.share_token}"}


@router.delete("/{run_id}/share", status_code=204)
def revoke_share(run_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    run = db.query(TestRun).filter(TestRun.id == run_id, TestRun.owner_id == user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Прогон не найден")
    run.share_token = None
    db.commit()


@router.get("/{run_id}/bugreport")
def bug_report(run_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Markdown bug report for all failed cases of a run."""
    run = db.query(TestRun).filter(TestRun.id == run_id, TestRun.owner_id == user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Прогон не найден")
    lines = [
        f"# Баг-репорт: {run.name}",
        "",
        f"- **URL:** {run.url}",
        f"- **Дата:** {(run.finished_at or run.created_at).isoformat() if (run.finished_at or run.created_at) else '—'}",
        f"- **Итог прогона:** {run.passed_steps}/{run.total_steps} шагов прошло",
        f"- **Ссылка на отчёт:** {run.share_token and f'/report/{run.share_token}' or 'не создана'}",
        "",
    ]
    failed = [s for s in run.steps
              if (s.status.value if hasattr(s.status, "value") else s.status) == "failed"]
    if not failed:
        lines.append("Проваленных проверок нет — все тест-кейсы прошли.")
    from app.services.humanize import humanize_step

    result_steps = (run.result_json or {}).get("steps", []) or []
    for i, s in enumerate(failed, 1):
        raw = (result_steps[i - 1] if i - 1 < len(result_steps) else {})
        action_text = humanize_step(
            s.action, s.selector, s.value, raw.get("expected_effect", ""),
            form_data=raw.get("form_data"))
        lines += [
            f"## Дефект {i}: {s.expected or (s.action + ' ' + s.selector).strip()}",
            "",
            f"- **Действие:** {action_text}",
            f"- **Фактический результат:** {s.message}",
        ]
        if s.console_error:
            lines.append(f"- **Ошибки консоли:** ```{s.console_error[:500]}```")
        if s.screenshot_path and os.path.exists(s.screenshot_path):
            fname = os.path.basename(s.screenshot_path)
            lines.append(f"- **Скриншот:** `screenshots/{fname}`")
        lines += ["", "**Шаги воспроизведения:**",
                  f"1. Открыть {run.url}",
                  f"2. Выполнить действие: {action_text}",
                  "", "---", ""]
    content = "\n".join(lines)
    return Response(content=content, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="bugreport_{run.id}.md"'})


@router.get("", response_model=list[TestRunOut])
def list_runs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    runs = db.query(TestRun).filter(TestRun.owner_id == user.id).order_by(TestRun.created_at.desc()).limit(50).all()
    results = []
    for r in runs:
        out = _to_out(r)
        out.predicted_seconds = 0
        results.append(out)
    return results


@router.get("/{run_id}", response_model=TestRunDetail)
def get_run(run_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    run = db.query(TestRun).filter(TestRun.id == run_id, TestRun.owner_id == user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Прогон не найден")
    return _detail(run, db)


@router.post("/{run_id}/cancel")
def cancel_run(run_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    run = db.query(TestRun).filter(TestRun.id == run_id, TestRun.owner_id == user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Прогон не найден")
    if run.status in (RunStatus.running.value, RunStatus.queued.value):
        run.status = RunStatus.cancelled
        run.finished_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        db.commit()
        return {"message": "Прогон отменён"}
    return {"message": "Прогон уже завершён"}


@router.get("/{run_id}/report/{fmt}")
def download_report(run_id: str, fmt: str, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    if fmt not in ("json", "html", "pdf", "junit"):
        raise HTTPException(status_code=400, detail="Поддерживаемые форматы: json, html, pdf, junit")
    run = db.query(TestRun).filter(TestRun.id == run_id, TestRun.owner_id == user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Прогон не найден")
    try:
        content = export_report(run, fmt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    media = {
        "json": "application/json",
        "html": "text/html",
        "pdf": "application/pdf",
        "junit": "application/xml",
    }
    filename = f"report_{run.id}.{fmt}"
    return Response(
        content=content,
        media_type=media[fmt],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{run_id}/screenshot")
def get_screenshot(run_id: str, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    run = db.query(TestRun).filter(TestRun.id == run_id, TestRun.owner_id == user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Прогон не найден")
    for step in db.query(TestStep).filter(TestStep.test_run_id == run_id,
                                          TestStep.screenshot_path.isnot(None)).all():
        if os.path.exists(step.screenshot_path):
            with open(step.screenshot_path, "rb") as fh:
                return Response(content=fh.read(), media_type="image/png")
    raise HTTPException(status_code=404, detail="Скриншот не найден")