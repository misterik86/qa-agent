"""Messenger notifications: Telegram and VK."""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def send_telegram(chat_id: str, text: str) -> bool:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        logger.warning("Telegram bot token not configured")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(url, json={"chat_id": chat_id, "text": text})
            return resp.status_code == 200
    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)
        return False


def send_vk(user_id: str, text: str, token: str | None = None) -> bool:
    token = token or settings.VK_API_TOKEN
    if not token:
        logger.warning("VK API token not configured")
        return False
    url = "https://api.vk.com/method/messages.send"
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(url, data={
                "user_id": user_id,
                "message": text,
                "random_id": str(abs(hash(text)) % (10**10)),
                "access_token": token,
                "v": "5.131",
            })
            data = resp.json()
            return "error" not in data
    except Exception as exc:
        logger.error("VK send failed: %s", exc)
        return False


def format_test_result(name: str, status: str, passed: int, failed: int, url: str, report_link: str = "") -> str:
    emoji = "✅" if status == "passed" else "❌"
    return (
        f"{emoji} Тест: {name}\n"
        f"Статус: {status}\n"
        f"PASS: {passed}, FAIL: {failed}\n"
        f"URL: {url}"
        + (f"\nОтчёт: {report_link}" if report_link else "")
    )