"""Сканер — reads DOM, text and attributes of interactive elements."""
import logging
import re

logger = logging.getLogger(__name__)

SELECTORS = {
    "form": "form",
    "button": "button, input[type=button], input[type=submit], [role=button]",
    "link": "a[href]",
    "select": "select",
    "input": "input:not([type=hidden]):not([type=submit]):not([type=button])",
}


def scan_elements(dom_text: str, max_elements: int = 50) -> list[dict]:
    """Parse an HTML snapshot and return interactive elements (max `max_elements`).

    In full-page mode the sandbox returns a serialized list of elements directly;
    this is a fallback parser for textual DOM dumps.
    """
    elements: list[dict] = []
    # Look for serialized markers from the sandbox scanner if present
    for m in re.finditer(r"\{\"type\":\s*\"(form|button|link|select|input)\"[^}]*\}", dom_text):
        try:
            import json

            el = json.loads(m.group(0))
            elements.append(el)
        except Exception:
            continue
        if len(elements) >= max_elements:
            break
    return elements


def _is_serious(line: str) -> bool:
    low = (line or "").lower()
    return any(k in low for k in ("error", "exception", "failed", "uncaught",
                                  "referenceerror", "typeerror"))


def extract_console_errors(raw: str | list | None) -> list[str]:
    if isinstance(raw, list):
        return [line.strip() for line in raw if line and line.strip() and _is_serious(line)]
    errors = []
    for line in (raw or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        if _is_serious(line):
            errors.append(line)
    return errors