"""Two-level scenario validator: syntactic (rule-based) + semantic (AI).
Results cached in Redis: val:hash(text+rule_version), TTL 1h."""
import hashlib
import json
import logging
import re
import uuid

import redis as redis_lib

from app.config import settings

logger = logging.getLogger(__name__)

ACTION_VERBS = [
    "нажми", "нажмите", "кликни", "кликните", "введи", "введите", "проверь", "проверьте",
    "выбери", "выберите", "перейди", "перейдите", "заполни", "заполните", "отправь", "отправьте",
    "выполни", "выполните", "обнови", "обновите", "найди", "найдите", "сравни", "сравните", "зайди",
]

OBJECT_KEYWORDS = ["кнопк", "поле", "ссылк", "форм", "блок", "меню", "элемент", "вкладк", "окно", "список", "dropdown", "select"]

VAGUE_PHRASES = [
    "как-нибудь", "что-нибудь", "непонятно", "сделай красиво", "проверь всё", "всё подряд",
    "протестируй", "посмотри", "попробуй", "сделай хорошо", "разберись", "поиграйся",
]

REGISTRATION_PHRASES = ["зарегистрируй", "создай аккаунт", "зарегистрироваться", "регистрацию", "создай учётн", "create account", "sign up"]

LOGIN_VERBS = ["введи", "введите", "заполни", "заполните", "впиши"]
LOGIN_FIELDS = ["логин", "login", "email", "почт", "username", "user name", "пользовател"]
PASSWORD_FIELDS = ["парол", "password", "pass"]


def _redis() -> redis_lib.Redis | None:
    try:
        return redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=2)
    except Exception:
        return None


def _cache_key(normalized: str) -> str:
    h = hashlib.sha256(f"{normalized}:{settings.VALIDATOR_RULES_VERSION}".encode("utf-8")).hexdigest()
    return f"val:{h}"


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


class ValidationResult:
    def __init__(self, valid: bool, errors: list[str], suggestions: list[str] = None,
                 warnings: list[str] = None, semantic: bool = False):
        self.valid = valid
        self.errors = errors
        self.suggestions = suggestions or []
        self.warnings = warnings or []
        self.semantic = semantic

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "suggestions": self.suggestions,
            "warnings": self.warnings,
            "semantic": self.semantic,
        }


def syntax_validate(text: str) -> ValidationResult:
    """Syntactic validation without AI."""
    errors: list[str] = []
    warnings: list[str] = []
    lowered = text.lower()
    words = re.findall(r"[а-яёa-z0-9]+", lowered)

    if len(words) < 5:
        errors.append("Сценарий должен содержать минимум 5 слов.")

    if len(words) > settings.MAX_SCENARIO_WORDS:
        errors.append(f"Сценарий не должен превышать {settings.MAX_SCENARIO_WORDS} слов.")

    if not any(verb in lowered for verb in ACTION_VERBS):
        errors.append("В сценарии отсутствует глагол действия (нажми, введи, проверь, выбери, перейди и т.д.).")

    if not any(keyword in lowered for keyword in OBJECT_KEYWORDS):
        errors.append("Укажите объект действия (кнопка, поле, ссылка, форма).")

    for phrase in VAGUE_PHRASES:
        if phrase in lowered:
            errors.append(f"Обнаружена расплывчатая фраза: «{phrase}». Опишите действие конкретнее.")

    return ValidationResult(valid=not errors, errors=errors, warnings=warnings, semantic=False)


def _check_auth_credentials(text: str) -> list[str]:
    """If scenario involves login (requires credentials), ensure explicit login/password input commands."""
    lowered = text.lower()
    login_commands = [f"{verb} в {field}" for verb in LOGIN_VERBS for field in LOGIN_FIELDS]
    pass_commands = [f"{verb} в {field}" for verb in LOGIN_VERBS for field in PASSWORD_FIELDS]
    has_login = any(kw in lowered for kw in LOGIN_FIELDS)
    has_password = any(kw in lowered for kw in PASSWORD_FIELDS)
    has_login_input = any(cmd in lowered for cmd in login_commands) or any(
        kw in lowered for kw in ("логин ", "логин:", "логин="))
    has_pass_input = any(cmd in lowered for cmd in pass_commands) or any(
        kw in lowered for kw in ("пароль ", "пароль:", "пароль="))
    errors = []
    if has_login and not has_login_input:
        errors.append("Укажите явно команду ввода логина, например: «введи в поле логин user@mail.ru».")
    if has_password and not has_pass_input:
        errors.append("Укажите явно команду ввода пароля, например: «введи в поле пароль 123456».")
    return errors


def _semantic_validate(text: str) -> ValidationResult:
    from app.services.ai import ai_client

    cache = _redis()
    key = _cache_key(normalize(text))
    if cache:
        try:
            cached = cache.get(key)
            if cached:
                data = json.loads(cached)
                res = ValidationResult(**data)
                res.semantic = True
                return res
        except Exception:
            pass

    prompt = (
        "Ты — валидатор сценариев автоматизированного тестирования веб-приложений. "
        "Проверь, осмыслен ли сценарий и выполним ли он через Playwright. "
        "Ответь ТОЛЬКО JSON вида: {\"valid\": true/false, \"errors\": [...], \"suggestions\": [...]}. "
        "Если valid=false, укажи понятную пользователю рекомендацию.\n\nСценарий:\n" + text
    )
    raw = ai_client.chat("Ты валидатор тестовых сценариев. Отвечай только JSON.", prompt)
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("not a dict")
    except Exception:
        data = {"valid": True, "errors": [], "suggestions": []}

    res = ValidationResult(
        valid=bool(data.get("valid", True)),
        errors=[str(e) for e in data.get("errors", [])],
        suggestions=[str(s) for s in data.get("suggestions", [])],
        semantic=True,
    )
    if cache:
        try:
            cache.setex(key, 3600, json.dumps(res.to_dict(), ensure_ascii=False))
        except Exception:
            pass
    return res


def validate_scenario(text: str) -> dict:
    """Full two-level validation."""
    result = syntax_validate(text)

    auth_errors = _check_auth_credentials(text)
    if auth_errors:
        result.errors.extend(auth_errors)

    if result.errors:
        result.valid = False
        return result.to_dict()

    semantic = _semantic_validate(text)
    if semantic.errors:
        result.errors.extend(semantic.errors)
        result.suggestions.extend(semantic.suggestions)
    result.semantic = semantic.semantic
    result.valid = result.valid and semantic.valid
    return result.to_dict()


def check_registration_intent(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in REGISTRATION_PHRASES)


def invalidate_validation_cache() -> None:
    cache = _redis()
    if not cache:
        return
    try:
        for key in cache.scan_iter("val:*"):
            cache.delete(key)
    except Exception:
        pass