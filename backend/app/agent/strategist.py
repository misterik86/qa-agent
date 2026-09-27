"""Стратег — analysis of a page and generation of test cases (uses AI)."""
import json
import logging

from app.config import settings
from app.services.ai import ai_client

logger = logging.getLogger(__name__)

# Actions supported by the sandbox runner for full-page plans. `assert_text`
# is intentionally excluded: full-page testing targets interactive elements,
# and the AI often emits assert_text with a descriptive "expected" instead of
# literal page text, producing false failures.
FULL_PAGE_ACTIONS = {
    "click", "click_and_back", "check_form", "validate_field",
    "fill_form", "check_auth", "select", "goto",
}


class Strategist:
    """Generates a test plan for full-page mode based on the DOM snapshot."""

    def __init__(self) -> None:
        self.ai = ai_client

    def generate_plan(self, url: str, elements: list[dict]) -> list[dict]:
        """elements: [{type, selector, text, name, fields}] -> list of test cases."""
        prompt = (
            "Ты — стратег автоматизированного тестирования. Для каждого интерактивного элемента "
            "сгенерируй тест-кейсы по теории тестирования. Каждый тест-кейс — это атомарная проверка "
            "с ожидаемым результатом (expected) и типом эффекта (expected_effect).\n"
            "expected_effect для действия:\n"
            "- click/ссылка: 'navigation' | 'state_changed' | 'element_appears' | 'element_disappears' | 'text_appears' | 'text_disappears'\n"
            "- форма (check_form): 'form_submitted' — проверить отправку (URL сменился или появилось подтверждение)\n"
            "- поле (validate_field): 'validation_required' | 'validation_email' | 'validation_length' | 'validation_pattern' | 'validation_range'\n"
            "Для каждой формы обязательно включи негативные тест-кейсы валидации полей (пустое required поле, "
            "невалидный email, длина, pattern) перед позитивным кейсом отправки формы.\n"
            "Для формы входа (логин+пароль) добавь кейс action=check_auth — проверка, что неверные учётные данные отклоняются.\n"
            "Ответь ТОЛЬКО JSON-массивом вида: "
            '[{"element": "button#login", "type": "button|link|form|select", "action": "click|submit|validate", '
            '"steps": [{"action": "click|check_form|validate_field|fill_form|assert_text|check_auth", "selector": "...", '
            '"value": "...", "expected_effect": "...", "expected": "описание ожидаемого результата"}], '
            '"note": "..."}]\n'
            f"URL: {url}\nЭлементы:\n{json.dumps(elements, ensure_ascii=False)[:4000]}"
        )
        raw = self.ai.chat("Ты стратег тест-планов. Отвечай только JSON.", prompt)
        try:
            plan = json.loads(raw)
            if not isinstance(plan, list):
                raise ValueError("not a list")
            plan = self._normalize_plan(plan)
        except Exception as exc:
            logger.warning("Strategist AI parse failed (%s), generating heuristic plan", exc)
            plan = self._heuristic_plan(elements)
        return plan

    def _normalize_plan(self, plan: list) -> list[dict]:
        """Sanitize AI output: keep only valid cases/steps with supported actions."""
        clean = []
        for case in plan:
            if not isinstance(case, dict):
                continue
            steps = case.get("steps")
            if not isinstance(steps, list):
                continue
            kept = [s for s in steps
                    if isinstance(s, dict) and s.get("action") in FULL_PAGE_ACTIONS]
            if not kept:
                continue
            case = dict(case)
            case["steps"] = kept
            clean.append(case)
        return clean

    def _heuristic_plan(self, elements: list[dict]) -> list[dict]:
        plan = []
        for el in elements[:50]:
            etype = el.get("type", "button")
            sel = el.get("selector", "")
            if etype == "form":
                is_login = _looks_like_login_form(el)
                cases = self._form_cases(sel, el.get("fields", []),
                                         include_submit=not is_login)
                plan.extend(cases)
            elif etype == "link":
                href = (el.get("href") or "").strip()
                text = (el.get("text") or "").strip()
                if not text or href.startswith("#"):
                    continue
                plan.append({
                    "element": sel,
                    "type": "link",
                    "action": "click",
                    "steps": [{
                        "action": "click_and_back", "selector": sel, "value": "",
                        "expected_effect": "navigation",
                        "expected": f"Переход по ссылке «{text}» открывает страницу (HTTP 200) и возврат успешен",
                    }],
                })
            elif etype == "select":
                plan.append({
                    "element": sel,
                    "type": "select",
                    "action": "select",
                    "steps": [{
                        "action": "select", "selector": sel, "value": "",
                        "expected_effect": "state_changed",
                        "expected": "Выбор значения в выпадающем списке применяется (значение меняется)",
                    }],
                })
            else:
                plan.append({
                    "element": sel,
                    "type": "button",
                    "action": "click",
                    "steps": [{
                        "action": "click", "selector": sel, "value": "",
                        "expected_effect": "state_changed",
                        "expected": f"Клик по кнопке «{el.get('text', '')}» вызывает видимый эффект (изменение страницы или переход)",
                    }],
                })
        for el in elements[:50]:
            if el.get("type") == "form" and _looks_like_login_form(el):
                plan.append({
                    "element": el.get("selector", ""),
                    "type": "form",
                    "action": "auth",
                    "steps": [{
                        "action": "check_auth",
                        "selector": el.get("selector", ""),
                        "value": "",
                        "expected_effect": "login_success",
                        "expected": "Форма входа: неверные учётные данные должны блокироваться (страница не меняется)",
                    }],
                })
                break
        return plan

    def _form_cases(self, sel: str, fields: list[dict],
                    include_submit: bool = True) -> list[dict]:
        cases = []
        negatives = 0
        max_neg = getattr(settings, "NEG_MAX_PER_FORM", 8)

        def neg_cap_reached() -> bool:
            return negatives >= max_neg

        for f in fields:
            if not isinstance(f, dict):
                continue
            fname = f.get("name") or f.get("type") or ""
            if fname:
                fsel = f'{sel} [name="{fname}"]'
            else:
                fsel = f"{sel} {f.get('tag', 'input')}:nth-of-type({f.get('idx', 1)})"

            def add_negative(effect: str, value: str, expected: str) -> bool:
                nonlocal negatives
                if neg_cap_reached():
                    return False
                cases.append({
                    "element": sel, "type": "field", "action": "validate",
                    "steps": [{
                        "action": "validate_field", "selector": fsel, "value": value,
                        "expected_effect": effect,
                        "expected": expected,
                    }],
                })
                negatives += 1
                return True

            if f.get("required"):
                add_negative(
                    "validation_required", "",
                    f"Обязательное поле «{fname}»: пустое значение не отправляет форму")
            ftype = (f.get("type") or "").lower()
            if ftype == "email":
                add_negative(
                    "validation_email", "not-an-email",
                    f"Поле «{fname}»: невалидный email отклоняется")
            elif ftype == "tel" and not neg_cap_reached():
                pass
            if f.get("pattern") and not neg_cap_reached():
                add_negative(
                    "validation_pattern", "!!!",
                    f"Поле «{fname}»: значение не по формату (pattern) отклоняется")
            if f.get("minlength") and int(f.get("minlength")) > 0:
                short = "a" * max(0, int(f["minlength"]) - 1)
                add_negative(
                    "validation_length", short,
                    f"Поле «{fname}»: значение короче minlength ({f['minlength']}) отклоняется")
            if f.get("maxlength") and int(f.get("maxlength")) > 0:
                long_val = "a" * (int(f["maxlength"]) + 5)
                add_negative(
                    "validation_length", long_val,
                    f"Поле «{fname}»: значение длиннее maxlength ({f['maxlength']}) отклоняется")
            if ftype == "number":
                add_negative(
                    "validation_pattern", "abc",
                    f"Числовое поле «{fname}»: нечисловое значение отклоняется браузером")

        if include_submit:
            cases.append({
                "element": sel, "type": "form", "action": "submit",
                "steps": [{
                    "action": "fill_form", "selector": sel, "value": "",
                    "expected_effect": "state_changed",
                    "expected": "Форма заполнена валидными тестовыми данными",
                }, {
                    "action": "check_form", "selector": sel, "value": "",
                    "expected_effect": "form_submitted",
                    "expected": "Форма отправляется: появляется подтверждение или происходит переход",
                }],
            })
        return cases


def _looks_like_login_form(el: dict) -> bool:
    """Heuristic: a form with password field + username-like input (email/text)."""
    fields = el.get("fields") or []
    has_password = any((f.get("type") or "").lower() == "password" for f in fields)
    has_user = any((f.get("type") or "").lower() in ("text", "email") for f in fields)
    if not (has_password and has_user):
        return False
    s = (el.get("selector") or "").lower() + " ".join(
        (f.get("name") or "").lower() for f in fields if isinstance(f, dict))
    return any(k in s for k in ("login", "auth", "signin", "sign-in", "log-in",
                                 "войти", "вход", "username", "user-name", "email", "loginpassword"))