import json

from app.agent.dispatcher import Dispatcher
from app.agent.judge import Judge
from app.agent.runner import _sample_repetitive
from app.agent.strategist import Strategist, FULL_PAGE_ACTIONS
from app.services.limits import get_parallel_limit, get_test_limit
from app.services.time_prediction import predict_duration


def test_dispatcher_full_scenario():
    d = Dispatcher()
    text = 'Нажми на кнопку "Войти", введи в поле логин test@test.com, введи в поле пароль 123456, нажми "Отправить", проверь, что появилось сообщение "Добро пожаловать"'
    steps = d.parse(text)
    assert len(steps) >= 5
    assert steps[0]["action"] == "click"
    assert steps[0].get("expected_effect") == "state_changed"


def test_dispatcher_form_submit_maps_to_check_form():
    d = Dispatcher()
    steps = d.parse("Отправь форму, проверь что появилось сообщение Спасибо")
    assert steps[0]["action"] == "check_form"
    assert steps[0]["expected_effect"] == "form_submitted"


def test_dispatcher_goto_has_navigation_effect():
    d = Dispatcher()
    steps = d.parse("Перейди на https://example.com")
    assert steps[0]["action"] == "goto"
    assert steps[0]["expected_effect"] == "navigation"


def test_dispatcher_check_text_quoted():
    d = Dispatcher()
    steps = d.parse('Проверь, что появилось сообщение "Добро пожаловать"')
    assert steps[0]["action"] == "assert_text"
    assert steps[0]["expected"] == "Добро пожаловать"


def test_dispatcher_check_form_validation_not_literal_text():
    d = Dispatcher()
    steps = d.parse("Проверь формы username и password на валидность и длину ввода")
    assert steps, "инструкция валидации не должна отбрасываться целиком"
    assert steps[0]["action"] == "validate_form"
    assert "валидируется" in steps[0]["expected"]


def test_dispatcher_check_without_target_is_skipped():
    d = Dispatcher()
    assert d.parse("Убедись в корректности отображения") == []


def test_judge_evaluates():
    j = Judge()
    assert j.evaluate_step({"action": "assert_text", "expected": "Добро пожаловать"},
                           {"status": "ok", "found_text": "Добро пожаловать, User"})["status"] == "passed"
    assert j.overall([{"status": "passed"}, {"status": "failed"}]) == "failed"


def test_judge_uses_expected_as_test_case_title():
    j = Judge()
    res = j.evaluate_step({"action": "click", "expected": "Кнопка открывает модальное окно"},
                          {"status": "ok", "message": "Клик: действие произвело видимый эффект"})
    assert res["status"] == "passed"
    assert "Кнопка открывает модальное окно" in res["message"]


def test_runner_effect_check_state_changed():
    """Действие без видимого эффекта (URL и контент не изменились) — FAIL."""
    from sandbox.runner import _check_effect

    class _Page:
        def __init__(self, state):
            self._state = state
        def locator(self, sel):
            class _L:
                def __init__(self, n): self._n = n
                def count(self): return self._n
            return _L(0)

    before = {"url": "https://example.com", "title": "x", "text": "same", "forms": 1, "buttons": 1, "inputs": 1}
    page = _Page(before)

    class _AfterPage(_Page):
        def __init__(self):
            super().__init__(None)
        @property
        def url(self): return "https://example.com"
        def title(self): return "x"
    ap = _AfterPage()

    # state unchanged -> fail
    from sandbox import runner as _r
    # emulate by monkeypatching snapshot
    orig = _r._snapshot
    calls = {"n": 0}
    def fake_snapshot(p):
        calls["n"] += 1
        return {"url": "https://example.com", "title": "x", "text": "same",
                "forms": 1, "buttons": 1, "inputs": 1}
    _r._snapshot = fake_snapshot
    try:
        res = _check_effect(ap, before, {"expected_effect": "state_changed"}, "button")
        assert res["status"] == "failed"
        assert "эффект" in res["message"].lower()
        # navigation expected but url unchanged -> fail
        res2 = _check_effect(ap, before, {"expected_effect": "navigation"}, "a")
        assert res2["status"] == "failed"
    finally:
        _r._snapshot = orig


def test_runner_effect_check_navigation_ok(monkeypatch):
    """URL сменился — navigation PASS."""
    from sandbox import runner as _r
    before = {"url": "https://example.com", "title": "x", "text": "t", "forms": 1, "buttons": 1, "inputs": 1}
    after = {**before, "url": "https://example.com/next"}

    class _Page:
        @property
        def url(self): return "https://example.com/next"
        def title(self): return "x"
        def locator(self, sel):
            class _L:
                def count(self): return 0
            return _L()
    monkeypatch.setattr(_r, "_snapshot", lambda p: after)
    res = _r._check_effect(_Page(), before, {"expected_effect": "navigation"}, "a")
    assert res["status"] == "ok"
    assert "/next" in res["message"]


def test_runner_test_value_for():
    from sandbox.runner import _test_value_for
    assert _test_value_for("email", "") == "qa.test@example.com"
    assert _test_value_for("tel", "phone") == "+79000000000"
    assert _test_value_for("text", "имя") == "Тест Тестов"
    assert _test_value_for("password", "password") == "QaPass!2025"
    assert _test_value_for("text", "anything") == "Тестовые данные"


def test_time_prediction_formula():
    # base 5 + steps*2 + elements*0.5
    pred = 5 + 10 * 2 + 4 * 0.5
    assert pred == 27


def test_limits_for_plans():
    from app.models.user import Plan, User

    class _U:
        pass

    u = User(plan=Plan.pro.value, quota_month="2099-01", tests_used_this_month=50)
    assert get_test_limit(u) == 100
    assert get_parallel_limit(u) == 5
    u2 = User(plan=Plan.enterprise.value)
    assert get_test_limit(u2) is None
    assert get_parallel_limit(u2) == 20
    # Trial: capped at 20 runs, strictly sequential (1 parallel slot).
    from app.services.limits import can_run_tests
    from datetime import datetime, timezone

    cur = datetime.now(timezone.utc).strftime("%Y-%m")
    u3 = User(plan=Plan.trial.value, quota_month=cur, tests_used_this_month=19)
    assert get_test_limit(u3) == 20
    assert get_parallel_limit(u3) == 1
    assert can_run_tests(u3) is True
    u3.tests_used_this_month = 20
    assert can_run_tests(u3) is False


def test_extract_console_errors_accepts_list():
    from app.agent.scanner import extract_console_errors

    assert extract_console_errors(["error: boom", "TypeError: x"]) == ["error: boom", "TypeError: x"]
    assert extract_console_errors("error: boom\nTypeError: x") == ["error: boom", "TypeError: x"]
    assert extract_console_errors(None) == []


def test_full_page_returns_error_without_elements(monkeypatch):
    """Если сканер не нашёл элементов — статус error с понятным сообщением."""
    from app.agent.runner import run_full_page

    class _Nav:
        def execute(self, url, steps, timeout, full_page=False, login=None):
            return {"status": "ok", "steps": [{"status": "ok"}], "elements": [], "console_errors": []}

    res = run_full_page("https://example.com", test_run_id="x", keeper=None, navigator=_Nav())
    assert res["status"] == "error"
    assert "интерактивных" in res["error"]


def test_assemble_result_aligns_after_login_step():
    """Sandbox prepends a login case; plan steps must still map 1:1 (bug-fix)."""
    from app.agent.keeper import Keeper
    from app.agent.runner import _assemble_result

    steps = [
        {"action": "click", "selector": "button:has-text('a')", "value": "", "expected": "Шаг A"},
        {"action": "click", "selector": "button:has-text('b')", "value": "", "expected": "Шаг B"},
    ]
    result = {
        "status": "ok",
        "steps": [
            {"status": "ok", "message": "Вход выполнен успешно",
             "expected": "Вход с корректными учётными данными выполнен успешно",
             "action": "check_auth"},
            {"status": "ok", "message": "эффект подтверждён"},
            {"status": "failed", "message": "эффект не достигнут"},
        ],
        "console_errors": [],
    }
    out = _assemble_result(result, steps, Judge(), Keeper("x"), "https://example.com")
    assert [s["expected"] for s in out["steps"]] == [
        "Вход с корректными учётными данными выполнен успешно", "Шаг A", "Шаг B"]
    assert out["steps"][1]["status"] == "passed"
    assert out["steps"][2]["status"] == "failed"
    assert out["status"] == "failed"


def test_console_errors_turn_into_failed_cases():
    """A 401 in the browser console must fail the run and bump counters."""
    from app.agent.keeper import Keeper
    from app.agent.runner import _assemble_result, _attach_console_cases, _finalize_status

    steps = [{"action": "goto", "selector": "", "value": "", "expected": "Страница открывается"}]
    result = {
        "status": "ok",
        "steps": [{"status": "ok", "message": "ok"}],
        "console_errors": ["Failed to load resource: the server responded with a status of 401", "GET https://x/api 401"],
    }
    out = _assemble_result(result, steps, Judge(), Keeper("x"), "https://example.com")
    out["steps"] = _attach_console_cases(out["steps"], out["console_errors"])
    out["status"] = _finalize_status(out["steps"], "ok")
    assert out["status"] == "failed"
    added = [s for s in out["steps"] if s.get("action") == "console_check"]
    assert added, "console error must become a failed case"
    assert all(s["status"] == "failed" for s in added)
    # attaching again is idempotent (already covered)
    again = _attach_console_cases(out["steps"], out["console_errors"])
    assert len(again) == len(out["steps"])


def test_passed_step_with_own_console_error_becomes_failed():
    """A step that 'passed' but carries a serious console error of its own is a
    contradiction — it must flip to failed and must NOT add a duplicate case."""
    from app.agent.keeper import Keeper
    from app.agent.runner import _attach_console_cases

    err = "error: Failed to load resource: the server responded with a status of 401"
    steps = [
        {"order": 1, "action": "click", "selector": "", "value": "",
         "expected": "Клик по кнопке Login", "status": "passed",
         "message": "Клик по «Login»: Действие произвело видимый эффект",
         "console_error": err},
    ]
    out = _attach_console_cases(steps, [err])
    assert out[0]["status"] == "failed", "passed-шаг с консольной 401 обязан стать failed"
    assert "ошибка консоли" in out[0]["message"]
    # То же противоречие не создаёт отдельного failed-кейса (ошибка учтена)
    assert all(s.get("action") != "console_check" for s in out)
    assert len(out) == len(steps)


def test_strategist_normalize_plan_drops_assert_text_and_unknown():
    s = Strategist()
    plan = [
        {"element": "form", "type": "form", "action": "validate",
         "steps": [
             {"action": "assert_text", "selector": "", "value": "",
              "expected_effect": "text_appears",
              "expected": "Проверь формы username и password на валидность и длину ввода"},
             {"action": "validate_field", "selector": 'input[name="user-name"]', "value": "",
              "expected_effect": "validation_required", "expected": "Обязательное поле"},
             {"action": "teleport", "selector": "", "value": "", "expected_effect": "", "expected": ""},
         ]},
        {"element": "button#login", "type": "button", "action": "click",
         "steps": "not-a-list"},
    ]
    clean = s._normalize_plan(plan)
    assert len(clean) == 1
    actions = [st["action"] for st in clean[0]["steps"]]
    assert actions == ["validate_field"]


def test_sample_repetitive_collapses_identical_items():
    elements = []
    # 12 identical product cards -> should collapse to SAMPLE_COUNT (2).
    for i in range(12):
        elements.append({"type": "link", "selector": f"a:has-text('Товар {i}')",
                         "css": "product-card", "href": f"/p/{i}"})
    # 2 distinct-featured buttons -> untouched.
    elements.append({"type": "button", "selector": "button#buy", "css": "btn btn-primary"})
    elements.append({"type": "button", "selector": "button#cart", "css": "btn btn-outline"})
    # form must always survive sampling.
    elements.append({"type": "form", "selector": "form", "css": "login-form"})

    out = _sample_repetitive(elements)
    cards = [e for e in out if e.get("css") == "product-card"]
    assert len(cards) == 2, f"expected 2 sampled cards, got {len(cards)}"
    # generic class "btn" is treated as non-unique -> never collapsed
    btns = [e for e in out if e.get("type") == "button"]
    assert len(btns) == 2
    # forms are preserved
    assert any(e.get("type") == "form" for e in out)
