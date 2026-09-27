import json

from app.services.reports import generate_html, generate_json, generate_junit, generate_pdf


def _fake_run():
    from datetime import datetime, timezone
    from types import SimpleNamespace

    now = datetime.now(timezone.utc)
    step = SimpleNamespace(
        order=1, action="click", selector="#btn", value="", expected="ok",
        status="passed", message="ok", console_error="", screenshot_path=None,
        started_at=now, finished_at=now,
    )
    return SimpleNamespace(
        id="run1", name="Test", url="https://example.com", mode="specific",
        status="passed", console_errors="", error_message="",
        started_at=now, finished_at=now, created_at=now,
        total_steps=1, passed_steps=1, failed_steps=0,
        steps=[step], artifacts=[], duration_seconds=0.0,
    )


def test_json_report():
    data = json.loads(generate_json(_fake_run()))
    assert data["status"] == "passed"
    assert data["total_steps"] == 1
    assert data["passed_steps"] == 1


def test_html_report():
    html = generate_html(_fake_run()).decode("utf-8")
    assert "<html" in html
    assert "Отчёт" in html


def test_junit_report():
    xml = generate_junit(_fake_run()).decode("utf-8")
    assert "<testsuite" in xml


def test_pdf_report():
    pdf = generate_pdf(_fake_run())
    assert pdf.startswith(b"%PDF")