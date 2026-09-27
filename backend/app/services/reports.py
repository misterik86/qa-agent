"""Report generation: JSON, HTML, PDF, JUnit XML."""
import io
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors

from app.models.test_run import TestRun, RunStatus

STATUS_RU = {
    RunStatus.queued.value: "В очереди",
    RunStatus.running.value: "Выполняется",
    RunStatus.passed.value: "Успешно",
    RunStatus.failed.value: "Ошибка",
    RunStatus.cancelled.value: "Отменён",
    RunStatus.timeout.value: "Таймаут",
    RunStatus.limit_exceeded.value: "Превышен лимит",
    RunStatus.error.value: "Ошибка выполнения",
}


def build_report_data(run: TestRun) -> dict:
    steps = []
    for i, s in enumerate(run.steps, 1):
        steps.append({
            "order": i,
            "action": s.action,
            "selector": s.selector,
            "value": s.value,
            "expected": s.expected,
            "status": s.status.value if hasattr(s.status, "value") else s.status,
            "message": s.message,
            "console_error": s.console_error,
            "screenshot": s.screenshot_path,
            "test_case_id": getattr(s, "test_case_id", None),
            "duration_seconds": round((s.finished_at - s.started_at).total_seconds(), 2) if s.started_at and s.finished_at else None,
        })
    console_errors = [e for e in (run.console_errors or "").split("\n") if e.strip()]
    data = {
        "id": run.id,
        "name": run.name,
        "url": run.url,
        "mode": run.mode.value if hasattr(run.mode, "value") else run.mode,
        "status": run.status.value if hasattr(run.status, "value") else run.status,
        "status_label": STATUS_RU.get(run.status.value if hasattr(run.status, "value") else run.status, ""),
        "total_time_seconds": round(run.duration_seconds, 2),
        "total_steps": run.total_steps,
        "passed_steps": run.passed_steps,
        "failed_steps": run.failed_steps,
        "console_errors": console_errors,
        "error_message": run.error_message,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "steps": steps,
    }
    return data


def generate_json(run: TestRun) -> bytes:
    return json.dumps(build_report_data(run), ensure_ascii=False, indent=2).encode("utf-8")


def _esc(value) -> str:
    """Escape text for safe HTML embedding (prevents XSS in reports)."""
    if value is None:
        return ""
    return (str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


def generate_html(run: TestRun) -> bytes:
    data = build_report_data(run)
    steps_rows = ""
    for s in data["steps"]:
        badge = '<span class="badge bg-success">PASS</span>' if s["status"] == "passed" else (
            '<span class="badge bg-danger">FAIL</span>' if s["status"] == "failed" else
            '<span class="badge bg-secondary">%s</span>' % _esc(s["status"]))
        err = f'<pre class="text-danger">{_esc(s["console_error"])}</pre>' if s["console_error"] else ""
        shot = ""
        if s["screenshot"] and s["screenshot"].startswith(("/", "http")):
            shot = f'<div><img src="{_esc(s["screenshot"])}" class="img-fluid" style="max-width:300px"/></div>'
        steps_rows += f"""<tr>
            <td>{s["order"]}</td><td>{_esc(s["action"])}</td><td>{_esc(s["selector"])}</td>
            <td>{_esc(s["value"])}</td><td>{_esc(s["expected"])}</td><td>{badge}</td>
            <td>{_esc(s["message"])}</td><td>{err}{shot}</td></tr>"""
    console_block = ""
    if data["console_errors"]:
        console_block = "<h3>Ошибки консоли</h3><pre>" + _esc(chr(10).join(data["console_errors"])) + "</pre>"
    html = f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <title>Отчёт: {_esc(data['name'])}</title></head><body class="bg-light">
    <div class="container py-4">
      <h1>Отчёт о тестировании</h1>
      <div class="card mb-3"><div class="card-body">
        <table class="table table-sm">
          <tr><td>Сценарий</td><td>{_esc(data['name'])}</td></tr>
          <tr><td>URL</td><td><a href="{_esc(data['url'])}">{_esc(data['url'])}</a></td></tr>
          <tr><td>Режим</td><td>{_esc(data['mode'])}</td></tr>
          <tr><td>Статус</td><td><strong>{_esc(data['status_label'])}</strong></td></tr>
          <tr><td>Время</td><td>{_esc(data['total_time_seconds'])} с</td></tr>
          <tr><td>Шаги</td><td>{_esc(data['passed_steps'])} PASS / {_esc(data['failed_steps'])} FAIL (всего {_esc(data['total_steps'])})</td></tr>
        </table>
      </div></div>
      <h3>Шаги</h3>
      <table class="table table-striped"><thead><tr><th>#</th><th>Действие</th><th>Селектор</th><th>Значение</th>
      <th>Ожидание</th><th>Результат</th><th>Сообщение</th><th>Детали</th></tr></thead>
      <tbody>{steps_rows}</tbody></table>
      {console_block}
    </div></body></html>"""
    return html.encode("utf-8")


def _pdf_esc(value) -> str:
    """Escape for reportlab Paragraph markup."""
    if value is None:
        return ""
    return (str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def generate_pdf(run: TestRun) -> bytes:
    data = build_report_data(run)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []
    elements.append(Paragraph(_pdf_esc(f"Отчёт: {data['name']}"), styles["Title"]))
    elements.append(Spacer(1, 12))
    info = [
        ["Сценарий", _pdf_esc(data["name"])],
        ["URL", _pdf_esc(data["url"])],
        ["Режим", _pdf_esc(data["mode"])],
        ["Статус", _pdf_esc(data["status_label"])],
        ["Время", _pdf_esc(f"{data['total_time_seconds']} с")],
        ["Шаги", _pdf_esc(f"{data['passed_steps']} PASS / {data['failed_steps']} FAIL")],
    ]
    t = Table(info)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 16))
    elements.append(Paragraph("Шаги", styles["Heading2"]))
    step_rows = [["#", "Действие", "Селектор", "Значение", "Результат"]]
    for s in data["steps"]:
        step_rows.append([str(s["order"]), _pdf_esc(s["action"]), _pdf_esc(s["selector"]), _pdf_esc(s["value"]),
                          "PASS" if s["status"] == "passed" else "FAIL"])
    st = Table(step_rows, repeatRows=1)
    st.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
    ]))
    elements.append(st)
    if data["console_errors"]:
        elements.append(Paragraph("Ошибки консоли", styles["Heading2"]))
        elements.append(Paragraph(_pdf_esc("<br/>".join(data["console_errors"])), styles["BodyText"]))
    doc.build(elements)
    return buf.getvalue()


def generate_junit(run: TestRun) -> bytes:
    data = build_report_data(run)
    suite = ET.Element("testsuites")
    tsuite = ET.SubElement(suite, "testsuite", {
        "name": data["name"],
        "tests": str(data["total_steps"] or 1),
        "failures": str(data["failed_steps"]),
        "errors": str(len(data["console_errors"])),
        "time": f"{data['total_time_seconds']:.2f}",
    })
    for s in data["steps"]:
        case = ET.SubElement(tsuite, "testcase", {
            "classname": data["name"],
            "name": f"step_{s['order']}_{s['action']}",
            "time": f"{s.get('duration_seconds') or 0:.2f}",
        })
        if s["status"] == "failed":
            failure = ET.SubElement(case, "failure", {"message": s["message"]})
            failure.text = s["console_error"] or s["message"]
    if not data["steps"]:
        status = ET.SubElement(tsuite, "testcase", {"name": "overall", "classname": data["name"]})
        if data["status"] == "failed":
            ET.SubElement(status, "failure", {"message": data["error_message"]})
    return ET.tostring(suite, encoding="utf-8")


def export_report(run: TestRun, fmt: str) -> bytes:
    if fmt == "json":
        return generate_json(run)
    if fmt == "html":
        return generate_html(run)
    if fmt == "pdf":
        return generate_pdf(run)
    if fmt == "junit":
        return generate_junit(run)
    raise ValueError(f"Unknown format: {fmt}")