"""Site crawl orchestration, extended negatives, auto-chains."""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.config import settings

PAGE_HTML = """<html><body>
<button id="open">Открыть форму</button>
<div id="modal" style="display:none">
  <form onsubmit="event.preventDefault(); document.getElementById('ok').style.display='block'; return false;">
    <input name="name" required value="">
    <button type="submit">Отправить</button>
  </form>
</div>
<div id="ok" style="display:none">Спасибо, заявка отправлена!</div>
<script>
document.getElementById('open').onclick = function () {
  document.getElementById('modal').style.display = 'block';
};
</script>
</body></html>""".encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(PAGE_HTML)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def local_site():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}/"
    srv.shutdown()


# ---------- Crawler ----------

class _FakeNav:
    """Scripted navigator: per-URL scan/plan results."""

    def __init__(self, pages, plan_status="passed", n_steps=2):
        self.pages = pages  # url -> {"elements": [...], "links": [...]}
        self.calls = []
        self.plan_status = plan_status
        self.n_steps = n_steps

    def execute(self, url, steps, timeout, full_page=False, login=None):
        self.calls.append((url, steps[0].get("action"), full_page))
        info = self.pages.get(url, {"elements": []})
        if steps[0].get("action") == "scan":
            return {"status": "ok", "elements": info["elements"], "steps": [], "console_errors": []}
        out = [{"status": self.plan_status, "message": "m", "order": i + 1} for i in range(self.n_steps)]
        return {"status": self.plan_status, "steps": out, "console_errors": []}


def test_crawl_visits_internal_pages_and_tags_steps(monkeypatch):
    from app.agent.runner import run_site_crawl

    monkeypatch.setattr(settings, "CRAWL_MAX_PAGES", 5)
    monkeypatch.setattr(settings, "CRAWL_DEPTH", 2)

    nav = _FakeNav(pages={
        "http://s.test/": {
            "elements": [
                {"type": "link", "href": "/about", "text": "О нас"},
                {"type": "link", "href": "https://external.test/x", "text": "Внешняя"},
                {"type": "link", "href": "#anchor", "text": "Якорь"},
                {"type": "link", "href": "mailto:a@b.c", "text": "Почта"},
                {"type": "button", "selector": "button:has-text('Кнопка')", "text": "Кнопка"},
            ],
        },
        "http://s.test/about": {
            "elements": [
                {"type": "link", "href": "/contacts", "text": "Контакты"},
            ],
        },
    })

    res = run_site_crawl("http://s.test/", test_run_id="x", navigator=nav)
    visited = {c[0] for c in nav.calls if c[1] == "scan"}
    # depth 2 => start -> about (d1) -> contacts (d2); external/anchor/mailto skipped
    assert visited == {"http://s.test/", "http://s.test/about", "http://s.test/contacts"}
    assert all("external.test" not in u for u in visited)
    # every step tagged with its page (judge may fail synthetic bare outcomes — fine)
    assert res["steps"], "expected aggregated steps"
    pages_of_steps = {s.get("page") for s in res["steps"]}
    assert pages_of_steps <= visited


def test_crawl_respects_max_pages(monkeypatch):
    from app.agent.runner import run_site_crawl

    monkeypatch.setattr(settings, "CRAWL_MAX_PAGES", 2)
    monkeypatch.setattr(settings, "CRAWL_DEPTH", 3)

    pages = {f"http://s.test/p{i}": {"elements": [
        {"type": "link", "href": f"/p{i+1}", "text": f"Страница {i+1}"}]} for i in range(6)}
    pages["http://s.test/"] = {"elements": [{"type": "link", "href": "/p1", "text": "P1"}]}
    nav = _FakeNav(pages=pages)

    run_site_crawl("http://s.test/", test_run_id="x", navigator=nav)
    scanned = [c[0] for c in nav.calls if c[1] == "scan"]
    assert len(set(scanned)) <= 2


# ---------- Extended negatives ----------

def test_extended_negative_cases_maxlength_number():
    from app.agent.strategist import Strategist

    st = Strategist()
    cases = st._form_cases("form:nth-of-type(1)", fields=[
        {"tag": "input", "type": "number", "name": "age", "required": True,
         "minlength": 0, "maxlength": 0, "idx": 1},
        {"tag": "input", "type": "text", "name": "title", "required": False,
         "minlength": 0, "maxlength": 10, "pattern": "", "idx": 2},
    ])
    effects = [(c["steps"][0]["expected_effect"], c["steps"][0]["selector"]) for c in cases
               if c["type"] == "field"]
    # required for age
    assert any(e == "validation_required" and "[name=\"age\"]" in s for e, s in effects)
    # number badInput case
    assert any(e == "validation_pattern" and "[name=\"age\"]" in s for e, s in effects)
    # maxlength overflow case
    long_case = [c for c in cases if c["type"] == "field"
                 and "[name=\"title\"]" in c["steps"][0]["selector"]
                 and len(c["steps"][0]["value"]) > 10]
    assert long_case and long_case[0]["steps"][0]["expected_effect"] == "validation_length"
    # positive submit always present
    assert any(c["type"] == "form" for c in cases)


def test_negative_cap_per_form(monkeypatch):
    monkeypatch.setattr(settings, "NEG_MAX_PER_FORM", 2)
    from app.agent.strategist import Strategist

    st = Strategist()
    fields = [{"tag": "input", "type": "email", "name": f"f{i}", "required": True,
               "minlength": 3, "maxlength": 0, "idx": i + 1} for i in range(5)]
    cases = st._form_cases("form:nth-of-type(1)", fields)
    negatives = [c for c in cases if c["type"] == "field"]
    assert len(negatives) == 2


# ---------- Auto-chains (local browser) ----------

@pytest.mark.skipif(settings.SANDBOX_RUNNER != "local", reason="needs local playwright")
def test_chain_probe_fills_revealed_form(local_site):
    import sys

    sys.path.insert(0, r"C:\Users\alfap\Documents\QA_agent\sandbox")
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "runner_mod", r"C:\Users\alfap\Documents\QA_agent\sandbox\runner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    steps = [{
        "action": "click", "selector": "#open", "value": "",
        "expected_effect": "state_changed",
        "expected": "Кнопка открывает форму заявки",
    }]
    res = mod.run_tests(local_site, steps, timeout_seconds=60, full_page=True,
                        allowed_domains=[], chain_max_forms=3,
                        allow_private_hosts=True, private_host_whitelist=["127.0.0.1"])
    # Sandbox keeps chains nested inside the click outcome; backend expands them.
    assert len(res["steps"]) == 1
    chain = res["steps"][0].get("chain") or []
    assert len(chain) == 2
    assert any("Цепочка" in (c.get("message") or "") for c in chain)
    assert any(c.get("form_data") for c in chain)
    assert chain[-1]["status"] == "passed"  # revealed form submitted with visible effect
