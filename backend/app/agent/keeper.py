"""Хранитель — structured JSON logging of steps, screenshots, console errors."""
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class Keeper:
    def __init__(self, test_run_id: str) -> None:
        self.test_run_id = test_run_id
        self.events: list[dict] = []

    def log(self, event: str, data: dict[str, Any] | None = None) -> None:
        entry = {"event": event, "data": data or {}}
        self.events.append(entry)
        logger.info("[keeper %s] %s %s", self.test_run_id, event, json.dumps(data or {}, ensure_ascii=False))

    def dump(self) -> str:
        return json.dumps({"test_run_id": self.test_run_id, "events": self.events}, ensure_ascii=False, indent=2)