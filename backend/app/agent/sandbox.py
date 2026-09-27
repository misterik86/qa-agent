"""Песочница — runs each test in an isolated Docker container with CPU/RAM limits
and network policy. Falls back to local execution when Docker is unavailable."""
import json
import logging
import os
import subprocess
import sys
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


class SandboxExecutor:
    def __init__(self) -> None:
        self.mode = settings.SANDBOX_RUNNER

    def run(self, url: str, steps: list[dict], timeout_minutes: int,
            full_page: bool = False, login: dict | None = None) -> dict:
        payload = {
            "url": url,
            "steps": steps,
            "timeout_seconds": timeout_minutes * 60,
            "full_page": full_page,
            "max_elements": settings.FULL_PAGE_MAX_ELEMENTS,
            "allowed_domains": settings.allowed_domains,
            "allow_private_hosts": settings.ALLOW_PRIVATE_HOSTS,
            "private_host_whitelist": settings.private_whitelist,
            "chain_max_forms": settings.CHAIN_MAX_FORMS,
            "telemetry_blocklist": settings.telemetry_blocklist,
        }
        if login:
            payload["login"] = login
        if self.mode == "docker":
            try:
                return self._run_docker(payload, timeout_minutes, full_page)
            except Exception as exc:
                if settings.SANDBOX_ALLOW_LOCAL_FALLBACK:
                    logger.warning("Docker sandbox failed (%s), falling back to local", exc)
                    return self._run_local(payload)
                logger.exception("Docker sandbox failed and local fallback is disabled")
                return {"status": "error",
                        "error": f"Песочница Docker недоступна: {exc}",
                        "steps": [], "console_errors": []}
        return self._run_local(payload)

    def _run_docker(self, payload: dict, timeout_minutes: int, full_page: bool) -> dict:
        container_timeout = max(timeout_minutes, 1)
        image = settings.SANDBOX_IMAGE
        cmd = [
            "docker", "run", "--rm",
            "--network", settings.SANDBOX_NETWORK,
            "--cpus", settings.SANDBOX_CPU_LIMIT,
            "--memory", settings.SANDBOX_MEM_LIMIT,
            "-e", f"PAYLOAD={json.dumps(payload)}",
            "-e", "PYTHONUNBUFFERED=1",
            image,
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=container_timeout * 60)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return {"status": "timeout", "error": "Превышен таймаут контейнера", "steps": []}
        if proc.returncode != 0:
            # The runner exits 1 when the scenario failed; results are still in stdout.
            parsed = self._parse_output(stdout)
            if parsed.get("status") != "error" or "QA_RESULT" in stdout:
                return parsed
            return {"status": "error", "error": stderr[-2000:], "steps": []}
        return self._parse_output(stdout)

    def _run_local(self, payload: dict) -> dict:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "sandbox"))
        try:
            from runner import run_tests

            return run_tests(
                url=payload["url"],
                steps=payload["steps"],
                timeout_seconds=payload["timeout_seconds"],
                full_page=payload["full_page"],
                max_elements=payload["max_elements"],
                allowed_domains=payload["allowed_domains"],
                login=payload.get("login"),
                chain_max_forms=int(payload.get("chain_max_forms", 3)),
                telemetry_blocklist=payload.get("telemetry_blocklist", []),
            )
        except Exception as exc:
            logger.exception("local sandbox failed")
            return {"status": "error", "error": str(exc), "steps": []}

    def _parse_output(self, stdout: str) -> dict:
        # The runner prints a JSON line marked with RESULT_PREFIX
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("QA_RESULT:"):
                try:
                    return json.loads(line[len("QA_RESULT:"):])
                except Exception:
                    break
        return {"status": "error", "error": stdout[-2000:], "steps": []}