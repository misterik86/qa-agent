"""Диспетчер — break scenario text into atomic steps and manage ordering."""
import logging
import re

logger = logging.getLogger(__name__)

VERBS = ["нажми", "кликни", "нажать", "кликнуть", "введи", "введите", "ввести", "впиши",
         "проверь", "проверьте", "проверить", "выбери", "выберите", "выбрать", "перейди",
         "перейдите", "перейти", "заполни", "заполните", "отправь", "отправьте", "отправить",
         "выполни", "обнови", "обновите", "обновить", "найди", "сравни", "убедись", "укажи"]

FIELD_WORDS = [
    "логин", "login", "email", "почт", "имя", "фамили", "пароль", "password", "pass",
    "поиск", "телефон", "комментар", "сообщение", "адрес", "название",
    "код", "токен", "промокод", "сумма", "количество", "тема",
]


class Dispatcher:
    """Parses Russian scenario text into atomic steps understood by the Navigator."""

    def parse(self, text: str) -> list[dict]:
        text = text.strip().rstrip(".!;")
        segments = self._split(text)
        steps = []
        for seg in segments:
            seg = seg.strip()
            if not seg:
                continue
            step = self._parse_segment(seg)
            if step:
                if "__multi__" in step:
                    steps.extend(step["__multi__"])
                else:
                    steps.append(step)
        return steps

    def _split(self, text: str) -> list[str]:
        """Split into atomic commands. Split on commas/semicolons only when the
        following segment begins with an action verb (so 'проверь, что появилось
        сообщение...' stays together, and emails with dots are not broken)."""
        result = []
        current = ""
        quote = None
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]
            if ch in "\"«»'":
                quote = ch if quote is None else None
                current += ch
            elif ch in ",;" and quote is None:
                nxt = text[i + 1:]
                stripped = nxt.lstrip(" ")
                if stripped and self._starts_with_verb(stripped):
                    result.append(current)
                    current = ""
                    # skip separator and following spaces
                    while i + 1 < n and text[i + 1] in ",; ":
                        i += 1
                else:
                    current += ","
            elif ch == "." and quote is None:
                nxt = text[i + 1] if i + 1 < n else ""
                if nxt in (" ", "", "\n"):
                    result.append(current + ch)
                    current = ""
                else:
                    current += ch  # inside email/domain/URL
            elif ch == "\n" and quote is None:
                result.append(current)
                current = ""
            else:
                current += ch
            i += 1
        if current.strip():
            result.append(current)
        return result

    @staticmethod
    def _starts_with_verb(segment: str) -> bool:
        lower = segment.lower()
        for v in VERBS:
            if lower.startswith(v) or lower.startswith(v + " ") or lower.startswith(v + "\n"):
                return True
        return False

    def _parse_segment(self, seg: str) -> dict | None:
        lower = seg.lower()
        has_verb = any(v in lower for v in VERBS)
        if not has_verb:
            return None

        if any(v in lower for v in ("нажми", "кликни", "нажать", "кликнуть")):
            selector = self._extract_target(seg)
            return {"action": "click", "selector": selector, "value": "", "expected": "",
                    "expected_effect": "state_changed"}
        if "форм" in lower and any(v in lower for v in ("заполни", "заполните", "заполни форму")):
            # "заполни форму (и отправь её)" -> fill_form + check_form
            steps = [{"action": "fill_form", "selector": self._extract_target(seg) or "form",
                      "value": "", "expected": "Форма заполняется валидными данными",
                      "expected_effect": "none"}]
            if any(v in lower for v in ("отправь", "отправьте", "отправить", "подтверди")):
                steps.append({"action": "check_form",
                              "selector": self._extract_target(seg) or "form", "value": "",
                              "expected": "Форма отправляется, подтверждение появляется",
                              "expected_effect": "form_submitted"})
            return steps[0] if len(steps) == 1 else {"__multi__": steps}
        if any(v in lower for v in ("введи", "введите", "ввести", "впиши", "заполни", "заполните", "укажи")):
            target, value = self._extract_field(seg)
            return {"action": "fill", "selector": target, "value": value, "expected": "", "target": target}
        if any(v in lower for v in ("выбери", "выберите", "выбрать")):
            target, value = self._extract_field(seg)
            return {"action": "select", "selector": target, "value": value, "expected": ""}
        if any(v in lower for v in ("перейди", "перейдите", "перейти", "открой", "зайди")):
            url = self._extract_url(seg)
            return {"action": "goto", "selector": "", "value": url, "expected": "",
                    "expected_effect": "navigation"}
        if any(v in lower for v in ("отправь", "отправьте", "отправить", "выполни", "подтверди", "принять", "сохрани")):
            selector = self._extract_target(seg)
            if "форму" in lower or "форме" in lower or "форма" in lower:
                return {"action": "check_form", "selector": selector or "form", "value": "",
                        "expected": "Форма отправляется", "expected_effect": "form_submitted"}
            return {"action": "click", "selector": selector or "form", "value": "", "expected": "",
                    "expected_effect": "state_changed"}
        if any(v in lower for v in ("обнови", "обновите", "обновить", "перезагрузи")):
            return {"action": "reload", "selector": "", "value": "", "expected": ""}
        if any(v in lower for v in ("проверь", "проверьте", "проверить", "сравни", "найди", "убедись")):
            return self._parse_check(seg)
        return None

    def _extract_target(self, seg: str) -> str:
        m = re.search(r"[\"«'](.+?)[\"»']", seg)
        if m:
            return m.group(1)
        m = re.search(r"(?:кнопку|кнопка|кнопки|ссылку|ссылке|элемент)\s*[\"«']?(.+?)[\"»']?$", seg)
        if m:
            return m.group(1).strip()
        return ""

    def _extract_field(self, seg: str) -> tuple[str, str]:
        """Extract (field_name, value) from e.g. 'введи в поле логин test@test.com'."""
        # find quoted pairs like "логин" = "value" or quoted value
        quotes = re.findall(r"[\"«'](.+?)[\"»']", seg)
        target = ""
        value = ""
        if len(quotes) >= 2:
            target, value = quotes[-2], quotes[-1]
        elif len(quotes) == 1:
            value = quotes[-1]
            target = self._find_field_word(seg)
        else:
            # unquoted: 'в поле логин test@test.com'
            target, value = self._find_field_and_value(seg)
        return target, value

    def _find_field_word(self, seg: str) -> str:
        for w in FIELD_WORDS:
            if re.search(rf"\b{w}\w*\b", seg, re.IGNORECASE):
                return w
        return ""

    def _find_field_and_value(self, seg: str) -> tuple[str, str]:
        # Match: (в поле)? <field> <value>  where value is the rest after the field word
        pattern = re.compile(r"(?:в поле|в полях|в)\s+([а-яёa-z0-9_-]+)\s+(.+)$", re.IGNORECASE)
        m = pattern.search(seg)
        if m:
            field = m.group(1)
            value = m.group(2).strip().strip("\"'«»")
            # strip trailing prepositions
            value = re.sub(r"\s+(и|затем|потом|далее)$", "", value)
            return field, value
        # fallback: first known field word followed by value
        for w in FIELD_WORDS:
            m = re.search(rf"\b{w}\w*\b\s+(.+)$", seg, re.IGNORECASE)
            if m:
                return w, m.group(1).strip().strip("\"'«»")
        return "", ""

    def _extract_url(self, seg: str) -> str:
        m = re.search(r"https?://[^\s\"«»']+", seg)
        if m:
            return m.group(0)
        m = re.search(r"[\"«'](.+?)[\"»']", seg)
        return m.group(1) if m else ""

    def _parse_check(self, seg: str) -> dict:
        m = re.search(r"[\"«'](.+?)[\"»']", seg)
        expected = m.group(1) if m else ""
        if not expected:
            m = re.search(r"(?:появилось сообщение|появилась надпись|текст|есть)\s*[\"«']?(.+?)[\"»']?$", seg)
            if m:
                expected = m.group(1).strip()
        if expected:
            return {"action": "assert_text", "selector": "", "value": "", "expected": expected}
        # «Проверь формы/поля ... на валидность/длину/ввод» — валидация полей формы,
        # а не поиск литеральной строки и не отправка формы (никакого логина).
        if re.search(r"валидност|обязательн|форма|форм[уы]|пол[ея]|заполн|длин[ауы]|ввод", seg, re.IGNORECASE):
            return {"action": "validate_form", "selector": "form", "value": "",
                    "expected": "Форма валидируется: обязательность полей и длина ввода проверяются без отправки",
                    "expected_effect": "validation"}
        return None