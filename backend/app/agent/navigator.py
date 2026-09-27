"""Навигатор — performs actions through Playwright in the isolated sandbox."""
from typing import Any


class Navigator:
    """Wrapper around the sandbox runner (Docker container or local Playwright).

    The actual browser driving code lives in sandbox/runner.py; the Navigator sends
    a JSON step payload and receives a JSON result back.
    """

    def __init__(self, executor: Any) -> None:
        self.executor = executor

    def execute(self, url: str, steps: list[dict], timeout_minutes: int,
                full_page: bool = False, login: dict | None = None) -> dict:
        return self.executor.run(url, steps, timeout_minutes, full_page, login)