"""Судья — compares expected vs actual results."""
import logging

logger = logging.getLogger(__name__)


class Judge:
    def evaluate_step(self, step: dict, outcome: dict) -> dict:
        """outcome: {status, message, found_text, console_errors}
        Позитивный вердикт выносится только при достижении ожидаемого эффекта:
        runner уже проверил эффект (state_changed/navigation/...), здесь статус
        действия конвертируется в passed/failed и сообщение формулируется как
        результат тест-кейса."""
        action = step.get("action", "")
        expected = step.get("expected", "")
        status = outcome.get("status", "failed")
        message = outcome.get("message", "")

        if action == "assert_text" and expected:
            found = outcome.get("found_text", "") or ""
            if status == "ok" and expected.lower() in found.lower():
                return {"status": "passed", "message": f"Проверка текста пройдена: найден «{expected}»"}
            return {"status": "failed", "message": f"Проверка текста не пройдена: ожидалось «{expected}», найдено: {found or 'ничего'}"}

        if action in ("click", "goto", "reload", "click_and_back", "check_form", "fill", "select",
                      "validate_field", "validate_form", "fill_form"):
            if status == "ok":
                prefix = f"Тест-кейс «{expected}»: " if expected else ""
                return {"status": "passed", "message": f"{prefix}{message or 'Шаг выполнен, эффект подтверждён'}"}
            prefix = f"Тест-кейс «{expected}»: " if expected else ""
            return {"status": "failed", "message": f"{prefix}{message or 'Ожидаемый эффект не достигнут'}"}

        if status == "ok":
            return {"status": "passed", "message": message or "Шаг выполнен"}
        return {"status": "failed", "message": message or "Шаг не выполнен"}

    def overall(self, step_results: list[dict]) -> str:
        if any(r.get("status") == "failed" for r in step_results):
            return "failed"
        if all(r.get("status") == "passed" for r in step_results) and step_results:
            return "passed"
        return "failed"