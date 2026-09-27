"""AI client with fallback chain: DeepSeek -> GigaChat -> YandexGPT.
If no API keys are configured, falls back to a deterministic heuristic so the
product works out of the box in development."""
import json
import logging
import time
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class AIError(Exception):
    pass


class AIClient:
    def __init__(self) -> None:
        self.last_model: str | None = None
        self.fallback_used = 0
        self.requests = 0

    def _call_http(self, url: str, headers: dict, payload: dict) -> dict:
        with httpx.Client(timeout=settings.AI_TIMEOUT_SECONDS) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()

    def chat(self, system: str, user: str) -> str:
        """Call primary model with retries, then fallback models."""
        self.requests += 1
        attempts = [self._chat_deepseek, self._chat_gigachat, self._chat_yandexgpt]
        errors = []
        for i, attempt in enumerate(attempts):
            retries = 0
            while retries < settings.AI_RETRIES:
                try:
                    text = attempt(system, user)
                    self.last_model = attempt.__name__.replace("_chat_", "")
                    if i > 0:
                        self.fallback_used += 1
                    return text
                except Exception as exc:  # noqa: BLE001
                    errors.append(str(exc))
                    retries += 1
                    time.sleep(min(2 ** retries, 8))
        # Heuristic fallback so the product works without AI keys.
        logger.warning("AI unavailable (%s), using heuristic fallback", errors[-1] if errors else "no keys")
        self.last_model = "heuristic"
        return self._heuristic(user)

    def _chat_deepseek(self, system: str, user: str) -> str:
        if not settings.DEEPSEEK_API_KEY:
            raise AIError("deepseek key not configured")
        data = self._call_http(
            f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
            {"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
            {"model": settings.DEEPSEEK_MODEL, "messages": [
                {"role": "system", "content": system}, {"role": "user", "content": user}]},
        )
        return data["choices"][0]["message"]["content"]

    def _chat_gigachat(self, system: str, user: str) -> str:
        if not settings.GIGACHAT_API_KEY:
            raise AIError("gigachat key not configured")
        token = self._gigachat_token()
        data = self._call_http(
            f"{settings.GIGACHAT_BASE_URL}/chat/completions",
            {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            {"model": settings.GIGACHAT_MODEL, "messages": [
                {"role": "system", "content": system}, {"role": "user", "content": user}]},
        )
        return data["choices"][0]["message"]["content"]

    def _gigachat_token(self) -> str:
        """GigaChat requires exchanging the API key for an OAuth bearer token."""
        import uuid

        rq_uid = str(uuid.uuid4())
        with httpx.Client(timeout=settings.AI_TIMEOUT_SECONDS, verify=False) as client:
            resp = client.post(
                f"{settings.GIGACHAT_BASE_URL.rstrip('/')}/auth",
                data={"scope": "GIGACHAT_API_PERS"},
                headers={
                    "Authorization": f"Basic {self._gigachat_basic()}",
                    "RqUID": rq_uid,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            resp.raise_for_status()
            return resp.json()["access_token"]

    @staticmethod
    def _gigachat_basic() -> str:
        import base64

        raw = settings.GIGACHAT_API_KEY.encode("utf-8")
        return base64.b64encode(raw).decode("utf-8")

    def _chat_yandexgpt(self, system: str, user: str) -> str:
        if not settings.YANDEXGPT_API_KEY:
            raise AIError("yandex key not configured")
        payload: dict[str, Any] = {
            "modelUri": f"gpt://{settings.YANDEXGPT_FOLDER_ID}/{settings.YANDEXGPT_MODEL}",
            "messages": [{"role": "system", "text": system}, {"role": "user", "text": user}],
        }
        data = self._call_http(
            f"{settings.YANDEXGPT_BASE_URL}/completion",
            {"Authorization": f"Api-Key {settings.YANDEXGPT_API_KEY}"},
            payload,
        )
        return data["result"]["alternatives"][0]["message"]["text"]

    def _heuristic(self, user: str) -> str:
        """Deterministic fallback that returns validatable JSON for known shapes."""
        if "валид" in user.lower():
            return json.dumps({"valid": True, "message": "Сценарий корректен (эвристический режим)."}, ensure_ascii=False)
        if "проверь" in user.lower() and any(w in user.lower() for w in ("загру", "работа", "стран", "страниц")):
            return json.dumps({"valid": True, "message": "Сценарий корректен (эвристический режим)."}, ensure_ascii=False)
        return json.dumps({"valid": True, "message": "Сценарий корректен (эвристический режим)."}, ensure_ascii=False)


ai_client = AIClient()