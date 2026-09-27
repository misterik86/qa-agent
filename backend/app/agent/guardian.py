"""Страж — time monitoring, stops test on critical errors / timeout."""
import logging
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)


class Guardian:
    def __init__(self, timeout_seconds: int) -> None:
        self.timeout_seconds = timeout_seconds
        self._started = 0.0
        self._cancelled = False
        self._timer: threading.Timer | None = None
        self._on_timeout: Callable | None = None

    def start(self, on_timeout: Callable | None = None) -> None:
        self._started = time.time()
        self._on_timeout = on_timeout
        self._timer = threading.Timer(self.timeout_seconds, self._fire_timeout)
        self._timer.daemon = True
        self._timer.start()
        logger.info("Guardian armed for %s s", self.timeout_seconds)

    def _fire_timeout(self) -> None:
        if self._cancelled:
            return
        logger.warning("Guardian fired: test exceeded %s s", self.timeout_seconds)
        if self._on_timeout:
            try:
                self._on_timeout()
            except Exception:
                logger.exception("guardian on_timeout hook failed")

    def cancel(self) -> None:
        self._cancelled = True
        if self._timer:
            self._timer.cancel()

    def elapsed(self) -> float:
        return time.time() - self._started