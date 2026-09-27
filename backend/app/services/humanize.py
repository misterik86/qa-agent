"""Human-readable Russian descriptions of test steps (instead of raw selectors)."""
import re

FIELD_FRIENDLY = [
    ("password", "пароль"),
    ("pass", "пароль"),
    ("пароль", "пароль"),
    ("email", "email"),
    ("почт", "email"),
    ("e-mail", "email"),
    ("phone", "телефон"),
    ("телефон", "телефон"),
    ("contact", "контакт"),
    ("контакт", "контакт"),
    ("surname", "фамилия"),
    ("фамили", "фамилия"),
    ("lastname", "фамилия"),
    ("login", "логин"),
    ("логин", "логин"),
    ("city", "город"),
    ("город", "город"),
    ("message", "сообщение"),
    ("сообщени", "сообщение"),
    ("comment", "комментарий"),
    ("комментари", "комментарий"),
    ("name", "имя"),
    ("имя", "имя"),
]

MASKED = "........"
Q = chr(34)
SQ = chr(39)


def _strip_quotes(s):
    return (s or "").strip(Q + SQ + " «»").strip()


def _label_from_selector(selector):
    """Extract a human label from a CSS/Playwright selector."""
    sel = (selector or "").strip()
    if not sel:
        return ""
    m = re.search(r"has-text\((.+?)\)", sel)
    if m:
        return _strip_quotes(m.group(1))
    m = re.search(r"\[name=([^\]]+)\]", sel)
    if m:
        return "поле " + _strip_quotes(m.group(1))
    m = re.search(r"\[placeholder=([^\]]+)\]", sel)
    if m:
        return "поле " + _strip_quotes(m.group(1))
    if sel.startswith("form"):
        return "форма"
    if re.match(r"^a$|^a\b|^a\[|^a\.|^a:", sel):
        return "ссылка"
    m = re.match(r"^#([\w\-]+)$", sel)
    if m:
        return "элемент " + m.group(1)
    return ""


def _friendly_field(name, kind=""):
    n = (name or "").lower()
    # check longer/more specific keys first
    for key, friendly in FIELD_FRIENDLY:
        if key in n:
            return friendly
    return name or kind or "поле"


def _is_secret(field_name, kind):
    n = (field_name or "").lower()
    return kind == "password" or "pass" in n or "пароль" in n


def _format_form_data(form_data, mask):
    if not form_data:
        return ""
    parts = []
    for item in form_data:
        fname = _friendly_field(item.get("name", ""), item.get("kind", ""))
        value = item.get("value", "")
        if mask and _is_secret(item.get("name", ""), item.get("kind", "")):
            value = MASKED
        kind = item.get("kind")
        if kind == "select":
            parts.append(fname + (": вариант «%s»" % value if value else ": выбран вариант"))
        elif kind == "checkbox":
            parts.append("%s: %s" % (fname, value))
        else:
            parts.append("%s - %s" % (fname, value))
    return ", ".join(parts)


def _field_label(selector):
    lbl = _label_from_selector(selector)
    if lbl.startswith("поле "):
        inner = lbl[5:]
        return "поле " + _friendly_field(inner)
    return lbl


def humanize_step(action, selector="", value="", expected_effect="",
                  form_data=None, mask=False):
    action = (action or "").strip().lower()

    if action == "check_auth":
        return "Проверка формы входа: неверные учётные данные должны блокироваться"
    if action == "console_check":
        return "Ошибок в консоли браузера нет"
    if action == "scan":
        return "Сканирование страницы"
    if action == "goto":
        return ("Открытие страницы " + value) if value else "Открытие страницы"
    if action == "reload":
        return "Обновление страницы"
    if action == "click_and_back":
        label = _label_from_selector(selector)
        base = "Переход по ссылке «%s»" % label if label else "Переход по ссылке"
        return base + ", затем возврат назад (проверка доступности)"
    if action == "click":
        sel = (selector or "").strip()
        is_link = bool(re.match(r"^a$|^a\b|^a\[|^a\.|^a:", sel))
        label = _label_from_selector(selector)
        if is_link:
            base = "Переход по ссылке «%s»" % label if label else "Переход по ссылке"
        elif label:
            base = "Нажатие кнопки «%s»" % label
        else:
            fl = _field_label(selector)
            base = ("Нажатие: %s" % fl) if fl else "Нажатие по элементу"
        return base
    if action == "fill":
        fl = _field_label(selector)
        shown = MASKED if (mask and _is_secret(_raw_name(selector), "text")) else value
        return ("Заполнение %s значением «%s»" % (fl or "поля", shown))
    if action == "select":
        fl = _field_label(selector)
        return "Выбор варианта «%s» в списке %s" % (value, fl or "")
    if action == "assert_text":
        if value:
            return "Проверка текста на странице: ожидается «%s»" % value
        return "Проверка текста на странице"
    if action == "check_form":
        detail = _format_form_data(form_data, mask)
        text = "Отправка формы с валидными данными"
        if detail:
            text += ": " + detail
        return text + ", ожидается подтверждение отправки"
    if action == "fill_form":
        detail = _format_form_data(form_data, mask)
        if detail:
            return "Заполнение формы валидными данными: " + detail
        return "Заполнение формы валидными тестовыми данными"
    if action == "validate_form":
        return "Проверка валидации полей формы: обязательность, длина и формат ввода"
    if action == "validate_field":
        fl = _field_label(selector)
        effect = (expected_effect or "").lower()
        if effect.startswith("validation_required"):
            return "Негативная проверка: %s обязательно, отправка пустого значения" % (fl or "поле")
        if effect.startswith("validation_email"):
            return "Негативная проверка %s: некорректный email («%s»)" % (fl or "поля email", value)
        if effect.startswith("validation_pattern"):
            return "Негативная проверка %s: значение не по формату («%s»)" % (fl or "поля", value)
        if effect.startswith("validation_length"):
            return "Негативная проверка %s: слишком короткое значение («%s»)" % (fl or "поля", value)
        if effect.startswith("validation_range"):
            return "Негативная проверка числового %s: значение вне диапазона" % (fl or "поля")
        return "Негативная проверка %s" % (fl or "поля")
    # fallback
    return ("%s %s" % (action, selector or "")).strip()


def _raw_name(selector):
    m = re.search(r"\[name=([^\]]+)\]", selector or "")
    return _strip_quotes(m.group(1)) if m else ""


_COMMON_TAG = {"button": "кнопка", "a": "ссылка", "form": "форма",
               "input": "поле", "select": "список", "textarea": "поле",
               "label": "подпись", "div": "блок", "span": "элемент"}


def _extract_selector_label(sel: str) -> str:
    """Best-effort human label from a raw CSS/Playwright selector."""
    m = re.search(r":has-text\(\s*['\"]([^'\"]+)['\"]\s*\)", sel)
    if m:
        return m.group(1).strip()
    m = re.search(r"\[(?:name|placeholder|data-test|data-testid|id|aria-label)['\"]?\s*=[*^~]?\s*['\"]?([^'\"\]\s]+)", sel)
    if m:
        return m.group(1).strip()
    m = re.match(r"^([a-z]+)(?:\[|:|\.|#)", sel)
    if m:
        return _COMMON_TAG.get(m.group(1), m.group(1))
    m = re.match(r"^#([\w-]+)$", sel)
    if m:
        return m.group(1)
    return ""


def _looks_like_selector(inner: str) -> bool:
    if not inner:
        return True
    if any(ch in inner for ch in "[]#"):
        return True
    if re.search(r":(has-text|nth-of-type|nth-child|hover|active|visible|first|last|has)\b", inner):
        return True
    if inner.startswith(("xpath=", "//")):
        return True
    if re.match(r"^[a-z][\w.-]*(?:\[|:)", inner):
        return True
    return False


def _strip_selector_span(match):
    inner = match.group(1).strip()
    if not _looks_like_selector(inner):
        return match.group(0)
    lbl = _extract_selector_label(inner)
    return ("«%s»" % lbl) if lbl else ""


def strip_selectors(text: str) -> str:
    """Remove raw CSS/Playwright selectors from step messages so the
    «фактический результат» column reads human-friendly (bug-fix)."""
    text = re.sub("«([^»]+)»", _strip_selector_span, text or "")
    text = re.sub(r'"([^"]+)"', _strip_selector_span, text)
    text = re.sub(r"\[[a-z][\w-]*(?:[=*^~]['\"]?[^\[\]\"']*)?\]", "", text)
    text = re.sub(r":nth-of-type\([^)]*\)", "", text)
    text = re.sub(r":has-text\([^)]*\)", "", text)
    text = re.sub(r"\s{2,}", " ", text).replace(" ,", ",").strip(" ,-")
    return text
