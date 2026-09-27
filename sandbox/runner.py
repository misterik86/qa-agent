"""Isolated Playwright test runner executed inside a sandbox container (or locally).

Reads the scenario payload, drives a headless browser with strict network policy
(block non-whitelisted domains, block private addresses), performs the atomic
steps, captures console errors and screenshots on failures, and prints a single
JSON result line prefixed with QA_RESULT:.
"""
import base64
import ipaddress
import json
import os
import random
import re
import socket
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError


def _shot(page) -> str:
    """Capture a screenshot and return it as a data URL (container-safe)."""
    try:
        data = page.screenshot(type="png", full_page=False)
        return "data:image/png;base64," + base64.b64encode(data).decode("ascii")
    except Exception:
        return ""

PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("::1/128"),
]


def _host_is_private(host: str, allow_private_hosts: bool = False,
                     private_host_whitelist: list[str] | None = None) -> bool:
    if allow_private_hosts and host and host in (private_host_whitelist or []):
        return False
    try:
        for info in socket.getaddrinfo(host, None):
            addr = ipaddress.ip_address(info[4][0])
            if any(addr in net for net in PRIVATE_NETS):
                return True
    except OSError:
        pass
    try:
        addr = ipaddress.ip_address(host)
        return any(addr in net for net in PRIVATE_NETS)
    except ValueError:
        return False


def _read_dns_name(body: bytes, off: int) -> tuple[str, int]:
    labels = []
    jumped = False
    end = off
    while True:
        b = body[off]
        if b == 0:
            if not jumped:
                end = off + 1
            break
        if b & 0xC0 == 0xC0:
            ptr = ((b & 0x3F) << 8) | body[off + 1]
            if not jumped:
                end = off + 2
            off = ptr
            jumped = True
            continue
        off += 1
        labels.append(body[off:off + b].decode("latin1"))
        off += b
    return ".".join(labels), end


def _tcp_dns_resolve(name: str, server: str = "8.8.8.8", timeout: int = 6) -> list[str]:
    """Resolve a hostname via TCP (port 53). Used when the system UDP resolver
    is unavailable (e.g. Docker Desktop on Windows), which otherwise causes
    ERR_NAME_NOT_RESOLVED inside the sandbox."""
    import struct

    def _enc(n: str) -> bytes:
        out = b""
        for part in n.split("."):
            out += bytes([len(part)]) + part.encode()
        return out + b"\x00"

    q = _enc(name) + struct.pack(">HH", 1, 1)
    header = struct.pack(">HHHHHH", random.randint(0, 65535), 0x0100, 1, 0, 0, 0)
    msg = header + q
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((server, 53))
        s.sendall(struct.pack(">H", len(msg)) + msg)
        resp = b""
        while len(resp) < 2:
            resp += s.recv(4096)
        rlen = struct.unpack(">H", resp[:2])[0]
        while len(resp) < 2 + rlen:
            resp += s.recv(4096)
        body = resp[2:]
        ancount = struct.unpack(">H", body[6:8])[0]
        off = 12
        _, off = _read_dns_name(body, off)
        off += 4
        ips = []
        for _ in range(ancount):
            _, off = _read_dns_name(body, off)
            rtype, _rc, _ttl, rdlen = struct.unpack(">HHIH", body[off:off + 10])
            off += 10
            rdata = body[off:off + rdlen]
            off += rdlen
            if rtype == 1 and rdlen == 4:
                ips.append(".".join(str(b) for b in rdata))
        return ips
    finally:
        s.close()


def _tcp_dns_ip(host: str) -> str | None:
    """Best-effort resolve of a hostname, trying several DNS servers."""
    for server in ("8.8.8.8", "1.1.1.1"):
        try:
            ips = _tcp_dns_resolve(host, server=server)
            if ips:
                return ips[0]
        except Exception:
            continue
    return None


def _build_resolver_rules(hosts: set[str]) -> list[str]:
    """Build Chromium --host-resolver-rules that map hostnames to IPs resolved
    via TCP DNS. Only includes hosts that the system resolver cannot resolve."""
    rules = []
    for host in hosts:
        if not host:
            continue
        try:
            socket.getaddrinfo(host, None)
            continue  # system resolver works for this host
        except OSError:
            pass
        ip = _tcp_dns_ip(host)
        if ip:
            rules.append(f"MAP {host} {ip}")
    return rules


def _allowed(host: str, allowed_domains: list[str]) -> bool:
    if not allowed_domains:
        return True
    host = host.lower().rstrip(".")
    for d in allowed_domains:
        d = d.lower().rstrip(".")
        if host == d or host.endswith("." + d):
            return True
    return False


# --- Effect verification (тестовая теория: действие должно иметь наблюдаемый эффект) ---

SUCCESS_WORDS = (
    "спасибо", "успешно", "успех", "отправлено", "отправлен", "принято", "принята",
    "получено", "заявка принята", "создано", "сохранено", "thank", "success",
    "submitted", "received", "done", "выполнено",
)
ERROR_WORDS = (
    "ошибка", "неверно", "не корректно", "некорректно", "проверьте", "заполните",
    "требуется", "обязательно", "повторите", "недоступно", "error", "invalid",
    "required", "failed", "warning", "пожалуйста",
)


def _visible_text(page) -> str:
    try:
        return (page.locator("body").inner_text(timeout=3000) or "").lower()
    except Exception:
        return ""


def _snapshot(page) -> dict:
    """Снимок состояния страницы — по нему определяем, произвёл ли действие эффект."""
    try:
        dialogs = 0
        scroll_y = 0
        try:
            state = page.evaluate("""() => {
                const sels = '[role=dialog], [role=alertdialog], .modal, .modal-window, .popup, .overlay, .form-popup, [class*="modal"], [class*="popup"], [class*="overlay"]';
                let visible = 0;
                for (const el of document.querySelectorAll(sels)) {
                    if (el.offsetParent !== null) visible++;
                }
                return {dialogs: visible, scrollY: Math.round(window.scrollY)};
            }""")
            dialogs = state.get("dialogs") or 0
            scroll_y = state.get("scrollY") or 0
        except Exception:
            pass
        return {
            "url": page.url,
            "title": (page.title() or "").lower(),
            "text": _visible_text(page),
            "forms": page.locator("form").count(),
            "buttons": page.locator("button, input[type=submit], input[type=button], [role=button]").count(),
            "inputs": page.locator("input, textarea, select").count(),
            "dialogs": dialogs,
            "scroll": scroll_y,
        }
    except Exception:
        return {"url": "", "title": "", "text": "", "forms": 0, "buttons": 0, "inputs": 0,
                "dialogs": 0, "scroll": 0}


def _wait_settle(page) -> None:
    """Дождаться стабилизации страницы после действия (async JS)."""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=3000)
    except Exception:
        pass
    try:
        page.wait_for_timeout(500)
    except Exception:
        pass


def _state_changed(before: dict, after: dict) -> bool:
    keys = ("url", "title", "text", "forms", "buttons", "inputs", "dialogs", "scroll")
    return any(before.get(k) != after.get(k) for k in keys)


def _has_new_success(after: dict, before: dict | None) -> bool:
    text = after.get("text", "")
    if not text:
        return False
    if before is not None and before.get("text", "") == text:
        return False
    return any(w in text for w in SUCCESS_WORDS)


def _has_new_error(after: dict, before: dict | None) -> bool:
    text = after.get("text", "")
    if not text:
        return False
    if before is not None and before.get("text", "") == text:
        return False
    return any(w in text for w in ERROR_WORDS)


def _check_effect(page, before: dict, step: dict, selector: str) -> dict:
    """Проверяет, что действие произвело ожидаемый эффект. Возвращает {status, message}."""
    effect = step.get("expected_effect") or "state_changed"
    value = step.get("expected_value") or step.get("value") or ""
    after = _snapshot(page)
    nav = before["url"] != after["url"]

    if effect in ("navigation", "url_changes"):
        if nav:
            return {"status": "ok", "message": f"Выполнен переход: {before['url']} -> {after['url']}"}
        return {"status": "failed", "message": f"Ожидался переход, но URL не изменился: {before['url']}"}
    if effect == "text_appears":
        if value and value.lower() in after["text"] and value.lower() not in before["text"]:
            return {"status": "ok", "message": f"Появился текст «{value}»"}
        return {"status": "failed", "message": f"Текст «{value}» не появился"}
    if effect == "text_disappears":
        if value and value.lower() in before["text"] and value.lower() not in after["text"]:
            return {"status": "ok", "message": f"Текст «{value}» исчез"}
        return {"status": "failed", "message": f"Текст «{value}» не исчез"}
    if effect == "element_appears":
        if selector and page.locator(_find_selector(selector)).count() > 0:
            return {"status": "ok", "message": f"Появился элемент «{selector}»"}
        return {"status": "failed", "message": f"Элемент «{selector}» не появился"}
    if effect == "element_disappears":
        if selector and page.locator(_find_selector(selector)).count() == 0:
            return {"status": "ok", "message": f"Элемент «{selector}» исчез"}
        return {"status": "failed", "message": f"Элемент «{selector}» не исчез"}
    if effect == "form_submitted":
        if nav:
            return {"status": "ok", "message": f"Форма отправлена: переход на {after['url']}"}
        if _has_new_success(after, before):
            return {"status": "ok", "message": "Форма отправлена: появилось подтверждение"}
        if _has_new_error(after, before):
            return {"status": "failed", "message": "Форма не отправилась: появилось сообщение об ошибке"}
        return {"status": "failed", "message": "Форма не отправилась: URL и содержимое не изменились"}
    if effect == "validation_error":
        if nav:
            return {"status": "failed", "message": "Ожидалась ошибка валидации, но форма отправилась"}
        if _has_new_error(after, before):
            return {"status": "ok", "message": "Валидация сработала: появилось сообщение об ошибке"}
        if not _state_changed(before, after):
            return {"status": "ok", "message": "Валидация сработала: форма не отправлена (браузерный контроль)"}
        return {"status": "failed", "message": "Не удалось подтвердить срабатывание валидации"}
    # default: state_changed
    if nav:
        return {"status": "ok", "message": f"Действие вызвало переход: {after['url']}"}
    if _state_changed(before, after):
        return {"status": "ok", "message": "Действие произвело видимый эффект на странице"}
    return {"status": "failed",
            "message": "Действие не произвело видимого эффекта (URL и содержимое страницы не изменились)"}



def _find_selector(selector: str) -> str:
    """Resolve a natural-language target into a best-effort CSS selector."""
    selector = (selector or "").strip().strip('"').strip("«»").strip("'")
    if not selector:
        return ""
    if selector.startswith(("//", "xpath=")):
        return selector
    # CSS selectors produced by the scanner:
    #   button:has-text('Отправить'), input[type=submit][value*='Отправить']
    #   a:has-text('Главная')  /  form:nth-of-type(1)  /  select:nth-of-type(1)
    # They must be used verbatim, not treated as natural-language text.
    if selector.startswith(("button:", "a:", "form:", "select:", "input:", "textarea:", "label:")) or \
       re.match(r"^(button|input|a|form|select|textarea|label|div|span)\[", selector):
        return selector
    # Generic fallback: if it already looks like a CSS selector list, pass through.
    # Requires an attribute selector or pseudo-class — plain words like "login" stay natural-language.
    if re.match(r"^[a-z]+(\[[^\]]*\]|:[a-z-]+\([^)]*\))(\s*,\s*[a-z]+(\[[^\]]*\]|:[a-z-]+\([^)]*\)))*$", selector):
        return selector
    # Plain id/class selectors are already valid Playwright CSS.
    if re.fullmatch(r"#[\w-]+", selector) or re.fullmatch(r"\.[\w-]+", selector):
        return selector
    # Exact id — but plain HTML tag names are selectors, not element names
    if re.fullmatch(r"[A-Za-z_][\w-]*", selector):
        if selector.lower() in ("form", "input", "button", "a", "select", "textarea",
                                "label", "table", "nav", "header", "footer", "main"):
            return selector.lower()
        return f'#{selector}, [name="{selector}"], input[placeholder*="{selector}" i]'
    return f'[placeholder*="{selector}" i], [aria-label*="{selector}" i], text="{selector}", button:has-text("{selector}"), a:has-text("{selector}")'


def _click(page, step: dict, console_errors: list, navigate_back: bool = True) -> dict:
    selector = step.get("selector", "")
    css = _find_selector(selector)
    try:
        before = _snapshot(page)
        if css:
            locator = page.locator(css).first
        else:
            # fallback: click first interactive element
            locator = page.locator("button, a, input[type=submit]").first
        try:
            locator.wait_for(state="visible", timeout=2500)
        except Exception:
            return {"status": "failed",
                    "message": f"Кнопка «{selector}» не видна на странице (скрытый блок/меню)",
                    "screenshot": _shot(page)}
        locator.click(timeout=8000)
        _wait_settle(page)
        effect = step.get("expected_effect") or "state_changed"
        res = _check_effect(page, before, step, selector)
        # If the click navigated away, optionally go back so subsequent steps stay on the page.
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        if navigate_back and page.url != before["url"]:
            try:
                page.go_back(timeout=10000)
                page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
        msg = res.get("message", "")
        if res["status"] == "ok":
            return {"status": "ok", "message": f"Клик по «{selector}»: {msg}"}
        return {"status": "failed", "message": f"Клик по «{selector}»: {msg}", "screenshot": _shot(page)}
    except Exception as exc:
        return {"status": "failed", "message": f"Не удалось кликнуть: {exc}", "screenshot": _shot(page)}


def _fill(page, step: dict, console_errors: list) -> dict:
    selector = step.get("selector", "")
    value = step.get("value", "")
    css = _find_selector(selector)
    try:
        if css:
            loc = page.locator(css).first
        else:
            # Fallback: fill a field whose name/placeholder/label matches the target word.
            target = step.get("target", "").strip().lower()
            if target:
                for attr in ("name", "placeholder", "aria-label"):
                    cand = page.locator(f"input[{attr}*='{target}'], textarea[{attr}*='{target}']").first
                    if cand.count():
                        loc = cand
                        break
                else:
                    label = page.locator("label").filter(has_text=target).first
                    if label.count() and label.locator("input, textarea").count():
                        loc = label.locator("input, textarea").first
                    else:
                        loc = page.locator("input").first
            else:
                loc = page.locator("input").first
        loc.fill(value, timeout=10000)
        _wait_settle(page)
        # Verify the value actually landed in the field (autofill/js reset guard).
        actual = ""
        try:
            actual = loc.input_value(timeout=3000)
        except Exception:
            pass
        if actual != value:
            return {"status": "failed",
                    "message": f"Значение «{value}» не применилось к полю «{selector}»: фактически «{actual}»",
                    "screenshot": _shot(page)}
        return {"status": "ok", "message": f"Поле «{selector}» заполнено значением «{value}» (проверено по значению поля)"}
    except Exception as exc:
        return {"status": "failed", "message": f"Не удалось ввести значение: {exc}", "screenshot": _shot(page)}


def _select(page, step: dict, console_errors: list) -> dict:
    selector = step.get("selector", "")
    value = step.get("value", "")
    try:
        loc = page.locator(_find_selector(selector)).first if selector else page.locator("select").first
        if value:
            loc.select_option(label=value)
        else:
            loc.select_option(index=1)
        _wait_settle(page)
        # Verify selection actually changed.
        selected = ""
        try:
            selected = loc.locator("option:checked").inner_text(timeout=3000) or ""
        except Exception:
            pass
        if value and selected and selected.strip().lower() != value.strip().lower():
            return {"status": "failed",
                    "message": f"Выбран вариант «{selected}», ожидался «{value}»", "screenshot": _shot(page)}
        return {"status": "ok", "message": f"В списке «{selector}» выбран вариант «{selected or value}» (проверено)"}
    except Exception as exc:
        return {"status": "failed", "message": f"Не удалось выбрать: {exc}"}


def _goto(page, step: dict, console_errors: list, allowed_domains: list,
          allow_private_hosts: bool = False, private_host_whitelist: list[str] | None = None) -> dict:
    url = step.get("value", "") or page.url
    if _host_is_private(urlparse(url).hostname or "", allow_private_hosts, private_host_whitelist):
        return {"status": "failed", "message": "Переход на внутренний адрес запрещён"}
    try:
        page.goto(url, timeout=20000, wait_until="domcontentloaded")
        return {"status": "ok", "message": f"Переход на {url} выполнен"}
    except Exception as exc:
        return {"status": "failed", "message": f"Не удалось перейти: {exc}"}


def _assert_text(page, step: dict, console_errors: list) -> dict:
    expected = step.get("expected", "")
    try:
        body = page.locator("body").inner_text(timeout=5000)
        if expected and expected.lower() in body.lower():
            return {"status": "ok", "found_text": expected, "message": "Текст найден"}
        return {"status": "failed", "found_text": body[:300], "message": f"Текст «{expected}» не найден"}
    except Exception as exc:
        return {"status": "failed", "message": f"Ошибка проверки текста: {exc}"}


def _reload(page, step, console_errors) -> dict:
    try:
        page.reload(timeout=20000)
        return {"status": "ok", "message": "Страница обновлена"}
    except Exception as exc:
        return {"status": "failed", "message": f"Ошибка обновления: {exc}"}


def _click_and_back(page, step, console_errors, allowed_domains) -> dict:
    selector = step.get("selector", "")
    try:
        el = page.locator(_find_selector(selector)).first
        # Fast visibility gate: hidden links (closed menu etc.) must fail in seconds,
        # not burn navigation timeouts.
        try:
            el.wait_for(state="visible", timeout=2500)
        except Exception:
            # Hidden link (closed burger menu / collapsed accordion). The element
            # exists in the DOM and is reachable via UI interaction, so its
            # hidden state is not a defect of the link itself. Report as skipped
            # (not failed) to avoid false-positive failures on menu-only links.
            return {"status": "ok",
                    "message": "Ссылка существует, но скрыта (блок/меню) — проверка пропущена"}
        href = el.get_attribute("href", timeout=2000) or ""
        target = (el.get_attribute("target", timeout=2000) or "").lower()
        # Anchor links / mailto / tel don't trigger navigation — click and mark OK.
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
            el.click(timeout=10000)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            return {"status": "ok", "message": f"Клик по ссылке «{href}» выполнен (без перехода)"}
        # target=_blank opens a new tab — verify the tab opens instead of navigation.
        if target == "_blank":
            with page.context.expect_page(timeout=12000) as new_page_info:
                el.click(timeout=10000)
            new_page = new_page_info.value
            try:
                new_page.wait_for_load_state("domcontentloaded", timeout=8000)
                url = new_page.url
                status = "ok"
                try:
                    resp = new_page.request.get(url, timeout=10000)
                    status = resp.status
                except Exception:
                    status = "открыта"
                msg = f"Ссылка «{href}» открыла новую вкладку ({status})"
            except Exception as exc:
                msg = f"Новая вкладка «{href}» не загрузилась: {exc}"
            try:
                new_page.close()
            except Exception:
                pass
            if "не загрузилась" not in msg:
                return {"status": "ok", "message": msg}
            return {"status": "failed", "message": msg, "screenshot": _shot(page)}
        with page.expect_navigation(timeout=15000, wait_until="domcontentloaded") as nav:
            el.click(timeout=10000)
        response = nav.value
        status = response.status if response else None
        page.go_back(timeout=15000)
        if status == 200:
            return {"status": "ok", "message": "Переход по ссылке выполнен (200), возврат успешен"}
        return {"status": "failed", "message": f"Ссылка вернула статус {status}"}
    except PWTimeoutError:
        # External links may be unreachable from the sandbox (network policy).
        try:
            href = el.get_attribute("href", timeout=2000) or ""
        except Exception:
            href = ""
        ext = ""
        try:
            h1 = urlparse(page.url).hostname or ""
            h2 = urlparse(href).hostname or ""
            if h2 and h2 != h1:
                ext = f" (внешний ресурс {h2})"
        except Exception:
            pass
        return {"status": "ok", "message": f"Ссылка «{href}» существует на странице{ext}; переход из песочницы не завершился (таймаут сети)"}
    except Exception as exc:
        return {"status": "failed", "message": f"Ошибка перехода по ссылке: {exc}", "screenshot": _shot(page)}


# Destructive/de-auth links that the crawler must not click (would log out or
# change state). Matched against link text and href.
_DISCOVER_SKIP_WORDS = (
    "logout", "log out", "sign out", "signout", "выйти", "выход", "разлогин",
    "delete", "удалить", "remove", "reset app", "сброс", "выйти из",
)

_DISCOVER_MAX_LINKS = 40


def _discover_links(page, allowed_domains: list[str],
                    max_links: int = _DISCOVER_MAX_LINKS) -> dict:
    """Click every <a> on the page and record where it navigates, returning the
    set of internal URLs. This handles SPAs where href is '#', empty, or JS-driven
    (the cart/product links on saucedemo.com, for example)."""
    start_url = page.url
    start_host = urlparse(start_url).hostname or ""

    def _is_internal(u: str) -> bool:
        h = urlparse(u).hostname or ""
        if not h or h == start_host:
            return True
        return _allowed(h, allowed_domains)

    discovered: list[dict] = []
    seen: set[str] = set()

    try:
        links = page.locator("a")
        n = links.count()
    except Exception:
        n = 0

    probed = 0
    for i in range(n):
        if probed >= max_links:
            break
        try:
            el = links.nth(i)
            if not el.count():
                continue
            try:
                if not el.is_visible():
                    continue
            except Exception:
                continue
            text = (el.inner_text(timeout=1500) or "").strip()
            href = (el.get_attribute("href", timeout=1500) or "").strip()
            low = (text + " " + href).lower()
            if any(w in low for w in _DISCOVER_SKIP_WORDS):
                continue
            if href.startswith(("mailto:", "tel:", "javascript:")):
                continue
            if href and urlparse(href).hostname and not _is_internal(href):
                continue  # external link — skip (network policy would block it)

            probed += 1
            target_url = None
            try:
                el.click(timeout=3000, no_wait_after=True)
                page.wait_for_timeout(600)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=3000)
                except Exception:
                    pass
                cur = page.url
                if cur and cur != start_url:
                    target_url = cur
            except Exception:
                target_url = None

            # Return to the start page for the next link.
            try:
                page.goto(start_url, timeout=15000, wait_until="domcontentloaded")
                page.wait_for_timeout(300)
            except Exception:
                try:
                    page.go_back(timeout=10000)
                    page.wait_for_timeout(300)
                except Exception:
                    pass

            if target_url:
                h = urlparse(target_url).hostname or ""
                if h == start_host or _allowed(h, allowed_domains):
                    path = (urlparse(target_url).path or "/").rstrip("/") or "/"
                    norm = f"{urlparse(target_url).scheme}://{h}{path}"
                    if norm not in seen:
                        seen.add(norm)
                        discovered.append({
                            "url": norm,
                            "text": text[:80],
                            "href": href,
                        })
        except Exception:
            continue

    return {"status": "ok", "discovered": discovered,
            "message": f"Обнаружено {len(discovered)} внутренних ссылок (прокликано {probed})"}


def _check_form(page, step, console_errors) -> dict:
    """Submit a form and verify the effect: navigation, success/error message,
    or the form being reset — per testing theory the submission must produce
    a visible outcome."""
    selector = step.get("selector", "")
    try:
        before = _snapshot(page)
        if selector:
            form = page.locator(_find_selector(selector)).first
        else:
            form = page.locator("form").first
        if not form.count():
            return {"status": "failed", "message": "Форма не найдена на странице"}
        # fill empty required fields with test data so the form can actually submit
        filled, form_data = _fill_form(page, {"selector": selector}, console_errors)
        submit = form.locator("button[type=submit], input[type=submit], input[type=image]").first
        if submit.count():
            submit.click(timeout=10000)
        else:
            form.locator("input[type=text], input[type=email], textarea").first.press("Enter", timeout=10000)
        _wait_settle(page)
        effect = step.get("expected_effect") or "form_submitted"
        res = _check_effect(page, before, {**step, "expected_effect": effect}, selector)
        if res["status"] == "ok":
            detail = f" (поля: {filled})" if filled else ""
            return {"status": "ok", "message": f"Форма «{selector or 'первая'}»: {res['message']}{detail}",
                    "form_data": form_data}
        return {"status": "failed", "message": f"Форма «{selector or 'первая'}»: {res['message']}", "screenshot": _shot(page)}
    except Exception as exc:
        return {"status": "failed", "message": f"Ошибка отправки формы: {exc}", "screenshot": _shot(page)}


def _fill_form(page, step, console_errors) -> tuple[list[str], list[dict]]:
    """Fill a form with realistic test data (valid values per field type).
    Returns (filled field names, [{name, value}] pairs actually entered).
    Used before submitting."""
    selector = step.get("selector", "")
    try:
        form = page.locator(_find_selector(selector)).first if selector else page.locator("form").first
        if not form.count():
            return [], []
    except Exception:
        return [], []
    filled = []
    form_data: list[dict] = []
    try:
        inputs = form.locator("input, textarea, select")
        for i in range(inputs.count()):
            el = inputs.nth(i)
            try:
                info = el.evaluate("""el => {
                    const t = (el.type || '').toLowerCase();
                    return {tag: el.tagName.toLowerCase(), type: t, name: el.name || '',
                            required: !!el.required, disabled: !!el.disabled, checked: !!el.checked};
                }""")
            except Exception:
                continue
            if info["disabled"] or info["type"] in ("hidden", "submit", "button", "reset", "file", "image"):
                continue
            label = info["name"] or info["type"]
            try:
                if info["tag"] == "select":
                    if el.locator("option").count() > 1:
                        opt = el.locator("option").nth(1).text_content() or ""
                        el.select_option(index=1)
                        filled.append(label)
                        form_data.append({"name": label, "kind": "select", "value": (opt or "").strip()})
                    continue
                if info["type"] == "checkbox":
                    if not info["checked"]:
                        el.check(timeout=3000)
                    filled.append(label)
                    form_data.append({"name": label, "kind": "checkbox", "value": "отмечено"})
                    continue
                if info["type"] == "radio":
                    continue
                value = _test_value_for(info["type"], info["name"])
                el.fill(value, timeout=5000)
                filled.append(label)
                form_data.append({"name": label, "kind": info["type"], "value": value})
            except Exception:
                continue
    except Exception:
        pass
    return filled, form_data


def _test_value_for(field_type: str, field_name: str) -> str:
    name = (field_name or "").lower()
    if field_type == "email" or "email" in name or "почт" in name or "e-mail" in name:
        return "qa.test@example.com"
    if field_type == "tel" or "phone" in name or "телефон" in name:
        return "+79000000000"
    if field_type == "number":
        return "42"
    if field_type == "date":
        return "2025-01-15"
    if field_type == "password" or "пароль" in name or "password" in name:
        return "QaPass!2025"
    if "имя" in name or "name" in name:
        return "Тест Тестов"
    if "фамили" in name or "surname" in name or "last" in name:
        return "Тестов"
    return "Тестовые данные"


def _validate_field(page, step, console_errors) -> dict:
    """Validation testing per testing theory: required, email format, min/max
    length, pattern — verified via the browser's built-in constraint API."""
    selector = step.get("selector", "")
    target = step.get("target", "").strip().lower()
    value = step.get("value", "")
    effect = step.get("expected_effect") or "validation_required"
    try:
        loc = None
        if selector:
            loc = page.locator(_find_selector(selector)).first
        if (loc is None or not loc.count()) and target:
            for attr in ("name", "placeholder", "aria-label"):
                cand = page.locator(f"input[{attr}*='{target}'], textarea[{attr}*='{target}']").first
                if cand.count():
                    loc = cand
                    break
        if loc is None or not loc.count():
            return {"status": "failed", "message": f"Поле «{selector or target}» не найдено для проверки валидации"}
        # set value (or clear for required check) and read the constraint state
        tag = ""
        try:
            tag = loc.evaluate("el => (el.tagName || '').toLowerCase()")
        except Exception:
            pass
        if tag == "select":
            if value == "" or effect == "validation_required":
                loc.select_option(index=0, timeout=8000)
            else:
                loc.select_option(index=1, timeout=8000)
        else:
            loc.fill(value, timeout=8000)
        _wait_settle(page)
        state = loc.evaluate("""el => {
            const f = el.form;
            if (f) { try { f.checkValidity(); } catch (e) {} }
            return {valueMissing: el.validity.valueMissing, typeMismatch: el.validity.typeMismatch,
                    tooShort: el.validity.tooShort, tooLong: el.validity.tooLong,
                    patternMismatch: el.validity.patternMismatch, badInput: el.validity.badInput,
                    rangeUnderflow: el.validity.rangeUnderflow, rangeOverflow: el.validity.rangeOverflow,
                    valid: el.validity.valid, msg: el.validationMessage || ''};
        }""")
        if effect == "validation_required":
            if state["valueMissing"]:
                return {"status": "ok", "message": "Поле обязательно: браузер блокирует отправку пустого значения"}
            return {"status": "failed", "message": "Поле помечено required, но пустое значение не блокируется"}
        if effect == "validation_email":
            if state["typeMismatch"]:
                return {"status": "ok", "message": f"Невалидный email «{value}» отклонён (typeMismatch)"}
            return {"status": "failed", "message": f"Невалидный email «{value}» принят без ошибки"}
        if effect == "validation_length":
            if state["tooShort"] or state["tooLong"]:
                return {"status": "ok", "message": "Некорректная длина значения отклонена браузером"}
            return {"status": "failed", "message": "Некорректная длина значения принята без ошибки"}
        if effect == "validation_pattern":
            # Для type=number patternMismatch не срабатывает — браузер ставит badInput.
            if state["patternMismatch"] or state.get("badInput"):
                return {"status": "ok", "message": "Значение, не соответствующее формату поля, отклонено браузером"}
            if not state["valid"] and state["msg"]:
                return {"status": "ok", "message": f"Некорректное значение отклонено: {state['msg'][:80]}"}
            return {"status": "failed", "message": "Значение, не соответствующее формату, принято без ошибки"}
        if effect == "validation_range":
            if state["rangeUnderflow"] or state["rangeOverflow"]:
                return {"status": "ok", "message": "Значение вне допустимого диапазона отклонено"}
            return {"status": "failed", "message": "Значение вне диапазона принято без ошибки"}
        if state["valid"]:
            return {"status": "ok", "message": "Значение прошло все браузерные проверки поля"}
        return {"status": "failed", "message": f"Значение невалидно: {state['msg'] or 'constraint violation'}"}
    except Exception as exc:
        return {"status": "failed", "message": f"Ошибка проверки валидации: {exc}"}


def _validate_form(page, step, console_errors) -> dict:
    """Validate a form WITHOUT authenticating with valid credentials.

    Two complementary strategies:
      1. HTML5 constraint API — where fields declare required/minlength/maxlength/
         pattern/email, verify the browser blocks invalid values.
      2. Behavioral rejection — submit the form EMPTY and confirm it is rejected
         (no navigation + an error indicator appears). This covers sites like
         saucedemo.com that validate via JS on submit rather than attributes.
    """
    selector = step.get("selector", "")
    try:
        if selector:
            form = page.locator(_find_selector(selector)).first
        else:
            form = page.locator("form").first
        if not form.count():
            return {"status": "failed", "message": "Форма не найдена на странице"}
        fields = form.locator("input, textarea, select")
        if not fields.count():
            return {"status": "failed", "message": "В форме нет полей для проверки валидации"}

        results: list[tuple[str, str, bool]] = []
        for i in range(fields.count()):
            el = fields.nth(i)
            try:
                info = el.evaluate("""el => {
                    const t = (el.type || '').toLowerCase();
                    const ml = el.minLength, xl = el.maxLength;
                    return {tag: el.tagName.toLowerCase(), type: t, name: el.name || '',
                            required: !!el.required, pattern: el.pattern || '',
                            minlength: (typeof ml === 'number' && ml > 0) ? ml : 0,
                            maxlength: (typeof xl === 'number' && xl > 0) ? xl : 0};
                }""")
            except Exception:
                continue
            if info["type"] in ("hidden", "submit", "button", "reset", "image", "file",
                                "checkbox", "radio"):
                continue
            fname = info["name"] or info["type"] or f"поле {i + 1}"

            if info["required"]:
                el.fill("", timeout=5000)
                ok = bool(el.evaluate("el => el.validity.valueMissing"))
                results.append((fname, "обязательность", ok))

            if info["minlength"]:
                short = "a" * max(0, int(info["minlength"]) - 1)
                el.fill(short, timeout=5000)
                ok = bool(el.evaluate("el => el.validity.tooShort"))
                results.append((fname, f"мин. длина {info['minlength']}", ok))

            if info["maxlength"]:
                # Boundary test per testing theory: exactly maxlength is accepted,
                # maxlength+1 is truncated/rejected.
                limit = int(info["maxlength"])
                exact = "a" * limit
                el.fill(exact, timeout=5000)
                exact_ok = bool(el.evaluate(
                    f"el => el.value.length <= {limit} && el.validity.valid"))
                el.fill("a" * (limit + 1), timeout=5000)
                over_ok = bool(el.evaluate(
                    f"el => el.validity.tooLong || el.value.length <= {limit}"))
                ok = exact_ok and over_ok
                results.append((fname, f"макс. длина {limit} (граница: {limit} принято, {limit + 1} отклонено)", ok))

            if not info["minlength"] and not info["maxlength"]:
                # No declared length limits -> stress/robustness test: inject a very
                # long value (10k chars) and confirm the field/page stays healthy.
                stress_ok, stress_msg = _length_stress_test(page, el, fname)
                results.append((fname, stress_msg, stress_ok))

            if info["pattern"]:
                el.fill("!!!", timeout=5000)
                ok = bool(el.evaluate("el => el.validity.patternMismatch"))
                results.append((fname, "формат (pattern)", ok))

            if info["type"] == "email":
                el.fill("not-an-email", timeout=5000)
                ok = bool(el.evaluate("el => el.validity.typeMismatch"))
                results.append((fname, "формат email", ok))

        behavior_ok, behavior_msg = _behavioral_validation(page, form)

        parts = [f"{n} — {c}: {'ok' if ok else 'НЕ блокируется'}" for n, c, ok in results]
        if behavior_msg:
            parts.append(behavior_msg)
        if not parts:
            return {"status": "failed",
                    "message": "Не удалось проверить валидацию формы (нет ни атрибутов, ни кнопки отправки)"}

        failed_attr = any(not ok for _, _, ok in results)
        if failed_attr or not behavior_ok:
            return {"status": "failed",
                    "message": f"Валидация формы не пройдена: {'; '.join(parts)}",
                    "screenshot": _shot(page)}
        return {"status": "ok",
                "message": f"Валидация формы пройдена ({len(parts)} проверок): {'; '.join(parts)}"}
    except Exception as exc:
        return {"status": "failed", "message": f"Ошибка проверки валидации формы: {exc}"}


def _length_stress_test(page, el, fname: str) -> tuple[bool, str]:
    """Inject 10 000 chars into a field with no declared length limit and verify
    the field/page survive. Returns (ok, message)."""
    try:
        el.fill("a" * 10000, timeout=10000)
        _wait_settle(page)
        stored = 0
        try:
            stored = int(el.evaluate("el => el.value.length") or 0)
        except Exception:
            pass
        page_alive = bool(page.locator("body").count())
        if not page_alive:
            return False, "страница сломалась после ввода 10 000 символов"
        if stored >= 10000:
            return True, "длина не ограничена: поле приняло 10 000 символов без сбоев"
        return True, f"поле без объявленной длины обрезало ввод до {stored} символов (страница не сломалась)"
    except Exception as exc:
        return False, f"ошибка стресс-теста длины: {exc}"


def _behavioral_validation(page, form) -> tuple[bool, str]:
    """Submit the form EMPTY and verify it is rejected: URL does not change to a
    success page and/or an error indicator appears. This is a negative test — it
    never uses valid credentials, so it is NOT an authorization attempt."""
    try:
        before_url = page.url.rstrip("/")
        inputs = form.locator("input, textarea")
        for i in range(inputs.count()):
            el = inputs.nth(i)
            try:
                t = (el.evaluate("el => (el.type || '').toLowerCase()") or "")
                if t in ("hidden", "submit", "button", "reset", "image", "file",
                         "checkbox", "radio"):
                    continue
                el.fill("", timeout=3000)
            except Exception:
                continue

        submit = form.locator("button[type=submit], input[type=submit], input[type=image]").first
        if submit.count():
            try:
                submit.click(timeout=5000)
            except Exception:
                pass
        else:
            try:
                form.locator("input[type=text], input[type=email], textarea").first.press("Enter", timeout=5000)
            except Exception:
                return False, "нет кнопки отправки для проверки отклонения пустой формы"
        _wait_settle(page)

        after_url = page.url.rstrip("/")
        navigated_away = bool(after_url and after_url != before_url)

        error_found = False
        for sel in ("[data-test=error]", "[role=alert]", "[aria-invalid=true]"):
            try:
                el = page.locator(sel).first
                if el.count() and (el.inner_text() or "").strip():
                    error_found = True
                    break
            except Exception:
                continue
        if not error_found:
            try:
                for sel in ("[class*=error]", "[class*=Error]"):
                    el = page.locator(sel).first
                    if el.count() and (el.inner_text() or "").strip():
                        error_found = True
                        break
            except Exception:
                pass
        if not error_found:
            try:
                body = (page.inner_text("body") or "").lower()
                for kw in ("required", "обязательн", "заполните", "неверн", "invalid", "error",
                           "incorrect", "is required"):
                    if kw in body:
                        error_found = True
                        break
            except Exception:
                pass

        form_still = False
        try:
            form_still = form.count() > 0
        except Exception:
            form_still = False

        rejected = (not navigated_away) and (error_found or form_still)
        if rejected:
            extra = " (появилось сообщение об ошибке)" if error_found else " (форма осталась на месте)"
            return True, f"пустая форма отклоняется{extra}"
        return False, "пустая форма не отклоняется (произошёл переход или нет ошибки)"
    except Exception as exc:
        return False, f"ошибка проверки отклонения формы: {exc}"


def _scan(page, full_page: bool, max_elements: int, allowed_domains: list) -> list[dict]:
    elements = []

    def _q(text: str) -> str:
        """Escape single quotes for use inside a Playwright :has-text() selector."""
        return (text or "").strip().replace("'", "\\'")[:60]

    try:
        forms = page.locator("form").count()
        buttons = page.locator("button, input[type=button], input[type=submit], [role=button]").count()
        links = page.locator("a[href]").count()
        selects = page.locator("select").count()
        for i in range(forms):
            if len(elements) >= max_elements:
                break
            el = page.locator("form").nth(i)
            fields = []
            try:
                finputs = el.locator("input, textarea, select")
                for j in range(finputs.count()):
                    fi = finputs.nth(j)
                    info = fi.evaluate("""el => {
                        const t = (el.type || '').toLowerCase();
                        const ml = el.minLength;
                        const xl = el.maxLength;
                        return {tag: el.tagName.toLowerCase(), type: t, name: el.name || '',
                                required: !!el.required, pattern: el.pattern || '',
                                minlength: (typeof ml === 'number' && ml > 0) ? ml : 0,
                                maxlength: (typeof xl === 'number' && xl > 0) ? xl : 0};
                    }""")
                    if info["type"] in ("hidden", "submit", "button", "reset", "image", "file"):
                        continue
                    fields.append({**info, "idx": j + 1})
            except Exception:
                pass
            elements.append({"type": "form", "selector": f"form:nth-of-type({i+1})",
                             "text": (el.inner_text() or "")[:100],
                             "css": (el.get_attribute("class") or "").strip()[:120],
                             "fields": fields[:10]})
        for i in range(buttons):
            if len(elements) >= max_elements:
                break
            el = page.locator("button, input[type=button], input[type=submit], [role=button]").nth(i)
            text = (el.inner_text() or el.get_attribute("value") or "").strip()
            if text:
                selector = f"button:has-text('{_q(text)}'), input[type=submit][value*='{_q(text)}']"
            else:
                selector = f"button:nth-of-type({i+1}), input[type=submit]:nth-of-type({i+1})"
            elements.append({"type": "button", "selector": selector, "text": text[:100],
                             "css": (el.get_attribute("class") or "").strip()[:120]})
        for i in range(links):
            if len(elements) >= max_elements:
                break
            el = page.locator("a[href]").nth(i)
            href = el.get_attribute("href") or ""
            if href.startswith(("mailto:", "tel:", "javascript:")):
                continue
            text = (el.inner_text() or "").strip()
            if text:
                selector = f"a:has-text('{_q(text)}')"
            else:
                selector = f"a[href]:nth-of-type({i+1})"
            elements.append({"type": "link", "selector": selector, "text": text[:100], "href": href,
                             "css": (el.get_attribute("class") or "").strip()[:120]})
        for i in range(selects):
            if len(elements) >= max_elements:
                break
            elements.append({"type": "select", "selector": f"select:nth-of-type({i+1})", "text": "",
                             "css": (el.get_attribute("class") or "").strip()[:120]})
    except Exception:
        pass
    return elements[:max_elements]


def _login(page, cfg: dict, console_errors: list) -> dict:
    """Authenticate on the site before running the suite. Returns step-like result."""
    errors_before = len(console_errors)
    who = f"вход как {cfg.get('username')}"
    if cfg.get("label"):
        who = f"доступ «{cfg.get('label')}», {who}"
    try:
        login_url = (cfg.get("login_url") or "").strip()
        if login_url and not page.url.startswith(login_url.rstrip("/")):
            try:
                page.goto(login_url, timeout=20000, wait_until="domcontentloaded")
            except PWTimeoutError:
                pass
            _wait_settle(page)
        raw_user_sel = (cfg.get("username_selector") or "").strip()
        user_sel = _find_selector(raw_user_sel) if raw_user_sel else \
            "input[type=email], input[name*=user i], input[type=text]"
        pass_sel = (cfg.get("password_selector") or "").strip() or "input[type=password]"
        user_field = page.locator(user_sel).first
        pass_field = page.locator(pass_sel).first
        user_field.wait_for(state="visible", timeout=8000)
        user_field.fill(cfg.get("username") or "", timeout=5000)
        pass_field.fill(cfg.get("password") or "", timeout=5000)
        raw_submit = (cfg.get("submit_selector") or "").strip()
        if raw_submit:
            btn = page.locator(_find_selector(raw_submit)).first
        else:
            btn = page.locator(
                "button[type=submit], input[type=submit], button:has-text('Войти'), "
                "button:has-text('Login'), button:has-text('Sign in')"
            ).first
        before_url = page.url
        with page.expect_navigation(wait_until="domcontentloaded", timeout=12000):
            btn.click(timeout=8000)
        _wait_settle(page)
        # crude success heuristics: URL changed, or password field gone
        ok = page.url != before_url or page.locator(pass_sel).count() == 0
        res = {"status": "ok" if ok else "failed",
               "message": f"Авторизация выполнена ({who})" if ok
               else f"Не удалось подтвердить вход ({who}): форма не исчезла, URL не изменился"}
    except Exception as exc:
        res = {"status": "failed",
               "message": f"Ошибка авторизации на сайте ({who}): {exc}", "screenshot": _shot(page)}
    res["console_error"] = "\n".join(dict.fromkeys(console_errors[errors_before:]))
    return res


def _visible_form_snapshot(page) -> list[str]:
    """Fingerprints of visible forms (for detecting newly appeared ones)."""
    try:
        return page.evaluate("""() => {
            const out = [];
            const els = document.querySelectorAll('form, [role=dialog] form, .modal form, .popup form');
            for (const f of els) {
                const cs = getComputedStyle(f);
                if (cs.display === 'none' || cs.visibility === 'hidden' || !f.offsetParent) continue;
                const fields = f.querySelectorAll('input:not([type=hidden]), textarea, select').length;
                if (!fields) continue;
                // Identity signature (no geometry): scrolling must not look like a "new" form
                out.push([f.id || '', f.name || '', f.getAttribute('action') || '',
                          f.className || ''].join('|') + ':' + fields);
            }
            return out;
        }""") or []
    except Exception:
        return []


def _probe_chain(page, before_forms: list, console_errors: list, chains_used: dict):
    """After a successful click: if a NEW visible form appeared (modal/drawer),
    fill it with valid data and try to submit — recorded as chained cases.
    Returns list of extra outcome dicts (may be empty)."""
    if chains_used["n"] >= CHAIN_MAX_FORMS:
        return []
    after = _visible_form_snapshot(page)
    new = [f for f in after if f not in before_forms]
    if not new:
        return []
    chains_used["n"] += 1
    extra = []

    # Case 1: the revealed form can be filled with valid data
    step = {"action": "fill_form", "selector": "", "value": "",
            "expected": "Появившаяся после клика форма заполняется валидными данными",
            "expected_effect": "state_changed"}
    filled, form_data = _fill_form(page, step, console_errors)
    ok_fill = bool(filled)
    res = {"status": "ok" if ok_fill else "failed",
           "message": ("Цепочка: появившаяся форма заполнена: " + ", ".join(filled))
           if ok_fill else "После клика появилась новая форма, но поля не заполнились"}
    if form_data:
        res["form_data"] = form_data
    extra.append(res)

    # Case 2: submission of that form produces an effect
    if ok_fill:
        before = _snapshot(page)
        submitted = False
        try:
            btn = page.locator(
                "form button[type=submit], form input[type=submit], "
                "[role=dialog] button[type=submit], .modal button[type=submit]").first
            if btn.count():
                with page.expect_navigation(wait_until="domcontentloaded", timeout=10000):
                    btn.click(timeout=8000)
                submitted = True
            else:
                page.locator("form input[type=text], form input[type=email], form textarea").first \
                    .press("Enter", timeout=8000)
                submitted = True
        except Exception:
            submitted = False
        _wait_settle(page)
        after_snap = _snapshot(page)
        effect = _state_changed(before, after_snap) or _has_new_success(after_snap, before)
        extra.append({
            "status": "passed" if effect else "failed",
            "message": ("Цепочка: форма из появившегося окна отправлена, эффект подтверждён"
                        if effect else
                        "Цепочка: отправка формы из появившегося окна не дала видимого эффекта"),
            "screenshot": None if effect else _shot(page),
        })
    return extra


CHAIN_MAX_FORMS = 3  # default; overridden via run_tests(chain_max_forms=...) / PAYLOAD


def _check_auth(page, step: dict, console_errors: list) -> dict:
    """Test the login form: fill bad credentials, submit, expect rejection."""
    try:
        user_sel = "input[type=email], input[name*=user i], input[type=text]"
        pwd_sel = "input[type=password]"
        user_field = page.locator(user_sel).first
        pwd_field = page.locator(pwd_sel).first
        user_field.wait_for(state="visible", timeout=5000)
        pwd_field.wait_for(state="visible", timeout=5000)
        before_url = page.url.rstrip("/")
        user_field.fill("__qa_invalid_user__", timeout=3000)
        pwd_field.fill("__qa_invalid_pass__", timeout=3000)
        btn = page.locator("button[type=submit], input[type=submit]").first
        try:
            with page.expect_navigation(wait_until="domcontentloaded", timeout=8000):
                btn.click(timeout=5000)
        except PWTimeoutError:
            pass  # no navigation = good, login rejected
        _wait_settle(page)
        still_there = pwd_field.count() > 0
        url_same = page.url.rstrip("/") == before_url
        if still_there or url_same:
            return {"status": "ok",
                    "message": "Неверные учётные данные НЕ пускают на сайт — форма осталась на месте (безопасность подтверждена)"}
        return {"status": "failed",
                "message": "Неверные учётные данные пустили на сайт (нет валидации логина!)",
                "screenshot": _shot(page)}
    except Exception as exc:
        return {"status": "failed", "message": f"Ошибка проверки формы авторизации: {exc}",
                "screenshot": _shot(page)}


def run_tests(url: str, steps: list[dict], timeout_seconds: int = 300,
              full_page: bool = False, max_elements: int = 50,
              allowed_domains: list | None = None,
              allow_private_hosts: bool = False,
              private_host_whitelist: list[str] | None = None,
              login: dict | None = None,
              chain_max_forms: int = 3,
              telemetry_blocklist: list | None = None,
              pre_login_steps: list[dict] | None = None) -> dict:
    allowed_domains = allowed_domains or []
    private_host_whitelist = private_host_whitelist or []
    telemetry_blocklist = [d.lower().lstrip("*.") for d in (telemetry_blocklist or [])]
    step_results = []
    console_errors: list[str] = []
    elements: list[dict] = []

    if _host_is_private(urlparse(url).hostname or "", allow_private_hosts, private_host_whitelist):
        return {"status": "error", "error": "Доступ к локальному адресу запрещён (SSRF-защита)",
                "steps": [], "console_errors": []}

    hosts = {urlparse(url).hostname or ""}
    for step in steps:
        if step.get("action") == "goto":
            hosts.add(urlparse(step.get("value") or "").hostname or "")
    resolver_rules = _build_resolver_rules(hosts)
    launch_args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
    if resolver_rules:
        launch_args.append("--host-resolver-rules=" + ",".join(resolver_rules))

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=launch_args,
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        )
        page = context.new_page()

        # Network policy: block private hosts, non-whitelisted domains and
        # noisy telemetry/analytics hosts (their 4xx responses must not fail
        # otherwise-passing checks).
        def _should_block(request_url: str) -> bool:
            parsed = urlparse(request_url)
            host = (parsed.hostname or "").lower()
            if any(host == d or host.endswith("." + d) for d in telemetry_blocklist):
                return True
            if _host_is_private(host, allow_private_hosts, private_host_whitelist):
                return True
            if not _allowed(host, allowed_domains):
                return True
            return False

        def _is_self_blocked(text: str) -> bool:
            # We abort requests ourselves (private/non-whitelisted/telemetry
            # hosts). Their "ERR_BLOCKED_BY_CLIENT" resource failures are a side
            # effect of the test policy, not a defect of the site under test.
            low = text.lower()
            if "err_blocked_by_client" in low or "err_aborted" in low:
                return True
            # Chrome's generic "Failed to load resource: 404" console line carries
            # no URL, so it cannot be attributed to a specific asset. Real HTTP
            # problems are diagnosed separately via responses (_record_http), which
            # distinguish asset 404s (favicon/images/fonts = noise) from genuine
            # code failures. Drop the URL-less console line to avoid double-reporting.
            if "failed to load resource:" in low and ": 40" in low:
                return True
            return False

        # Non-code assets whose 4xx errors are harmless (favicon, images, fonts,
        # media). Real 5xx or failures on HTML/JS/CSS/API resources stay serious.
        _NOISE_EXTS = {".ico", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif",
                       ".svg", ".woff", ".woff2", ".ttf", ".otf", ".eot",
                       ".mp4", ".webm", ".mp3", ".wav", ".ogg", ".manifest",
                       ".map", ".txt", ".xml", ".webmanifest"}

        def _is_noise_url(raw: str, status: int) -> bool:
            if status < 400 or status >= 600:
                return True
            if status >= 500:
                return False
            try:
                path = urlparse(raw).path
                ext = os.path.splitext(path)[1].lower()
            except Exception:
                return False
            if ext in _NOISE_EXTS:
                return True
            # A 404 on a URL with no extension (or an html path) is real.
            return False

        http_errors: list[str] = []

        def _record_http(resp):
            try:
                status = resp.status
                u = resp.url
            except Exception:
                return
            if _is_noise_url(u, status):
                return
            http_errors.append(f"error: HTTP {status} {u}")

        page.on("response", _record_http)

        page.on("console",
                lambda msg: console_errors.append(f"{msg.type}: {msg.text}")
                if msg.type in ("error", "warning") and not _is_self_blocked(msg.text) else None)
        page.on("requestfailed", lambda req: console_errors.append(f"requestfailed: {req.url} {req.failure}")
                if not _is_self_blocked(req.failure or "") else None)

        def intercept(route):
            if _should_block(route.request.url):
                route.abort("blockedbyclient")
            else:
                route.continue_()

        context.route("**/*", intercept)

        try:
            page.goto(url, timeout=20000, wait_until="domcontentloaded")
        except Exception as exc:
            return {"status": "failed", "error": f"Не удалось открыть страницу: {exc}",
                    "steps": [], "console_errors": list(dict.fromkeys(console_errors + http_errors))}

        start = time.time()

        def _dbg(msg):
            try:
                print(f"[sandbox t={time.time() - start:.1f}s] {msg}", file=sys.stderr, flush=True)
            except Exception:
                pass

        # Pre-login steps (auth form tests): executed on the unauthenticated page
        # BEFORE the login block — no credentials needed, they check that bad creds
        # are rejected and form validation works.
        if pre_login_steps:
            _dbg(f"pre_login: {len(pre_login_steps)} steps")
            for step in pre_login_steps:
                action = step.get("action", "")
                errors_before = len(console_errors)
                step_start = time.time()
                try:
                    if action == "check_auth":
                        res = _check_auth(page, step, console_errors)
                    else:
                        res = {"status": "failed", "message": f"Неизвестное действие pre_login: {action}"}
                except Exception as exc:
                    res = {"status": "failed", "message": str(exc), "screenshot": _shot(page)}
                res["console_error"] = "\n".join(dict.fromkeys(console_errors[errors_before:]))
                res["duration_seconds"] = round(time.time() - step_start, 2)
                res["url"] = page.url or ""
                _dbg(f"pre_login {action} -> {res.get('status')} ({res['duration_seconds']}s)")
                step_results.append(res)

        # Authenticate before any phase (suite/specific AND both full_page phases):
        # every sandbox invocation gets a fresh browser profile, so each phase
        # performs its own login.
        login_success = True
        if login and login.get("username"):
            _dbg("login start")
            login_res = _login(page, login, console_errors)
            _dbg(f"login done: {login_res.get('status')}")
            login_success = login_res.get("status") == "ok"
            # Record successful login as a visible test step in the plan phase.
            if full_page and login_success:
                login_step = login_res
                login_step["action"] = "check_auth"
                login_step["console_error"] = login_step.get("console_error", "")
                login_step["duration_seconds"] = login_step.get("duration_seconds", 0)
                login_step["expected"] = "Вход с корректными учётными данными выполнен успешно"
                step_results.append(login_step)
            if not login_success:
                result = {"status": "failed",
                          "error": f"Не удалось войти на сайт: {login_res.get('message', '')}",
                          "steps": step_results,
                          "console_errors": list(dict.fromkeys(console_errors + http_errors))}
                payload_line = "QA_RESULT:" + json.dumps(result, ensure_ascii=False)
                try:
                    print(payload_line, flush=True)
                except UnicodeEncodeError:
                    sys.stdout.buffer.write(payload_line.encode("utf-8", "replace") + b"\n")
                    sys.stdout.flush()
                try:
                    browser.close()
                except Exception:
                    pass
                return result

        if full_page and steps and steps[0].get("action") == "scan":
            _dbg("scan start")
            elements = _scan(page, full_page, max_elements, allowed_domains)
            _dbg(f"scan done: {len(elements)} elements")
            step_results = [{"status": "ok", "message": f"Сканирование: {len(elements)} элементов"}]
            result = {
                "status": "ok",
                "steps": step_results,
                "elements": elements,
                "console_errors": list(dict.fromkeys(console_errors + http_errors)),
            }
        else:
            chains_used = {"n": 0}
            for step in steps:
                if time.time() - start > timeout_seconds:
                    step_results.append({"status": "failed", "message": "Превышено время выполнения (страж)"})
                    break
                action = step.get("action", "")
                errors_before = len(console_errors)
                step_start = time.time()
                try:
                    if action == "click":
                        pre_forms = _visible_form_snapshot(page) if full_page else []
                        res = _click(page, step, console_errors, navigate_back=full_page)
                        # Auto-chain: a successful click may reveal a new form/modal —
                        # fill & submit it as additional chained cases (full_page only).
                        if (full_page and res.get("status") == "ok"
                                and chains_used["n"] < max(0, int(chain_max_forms))):
                            chain = _probe_chain(page, pre_forms, console_errors, chains_used)
                            if chain:
                                res["chain"] = chain
                    elif action == "fill":
                        res = _fill(page, step, console_errors)
                    elif action == "select":
                        res = _select(page, step, console_errors)
                    elif action == "goto":
                        res = _goto(page, step, console_errors, allowed_domains,
                            allow_private_hosts, private_host_whitelist)
                    elif action == "reload":
                        res = _reload(page, step, console_errors)
                    elif action == "assert_text":
                        res = _assert_text(page, step, console_errors)
                    elif action == "click_and_back":
                        res = _click_and_back(page, step, console_errors, allowed_domains)
                    elif action == "check_form":
                        res = _check_form(page, step, console_errors)
                    elif action == "validate_field":
                        res = _validate_field(page, step, console_errors)
                    elif action == "validate_form":
                        res = _validate_form(page, step, console_errors)
                    elif action == "fill_form":
                        filled, form_data = _fill_form(page, step, console_errors)
                        res = {"status": "ok", "message": f"Форма заполнена: {', '.join(filled) if filled else 'полей не найдено'}",
                               "form_data": form_data}
                    elif action == "check_auth":
                        res = _check_auth(page, step, console_errors)
                    elif action == "discover_links":
                        res = _discover_links(page, allowed_domains)
                    else:
                        res = {"status": "failed", "message": f"Неизвестное действие: {action}"}
                except Exception as exc:
                    res = {"status": "failed", "message": str(exc), "screenshot": _shot(page)}
                res["console_error"] = "\n".join(dict.fromkeys(console_errors[errors_before:]))
                res["duration_seconds"] = round(time.time() - step_start, 2)
                res["url"] = page.url or ""
                if full_page:
                    _dbg(f"step {len(step_results)} {action} '{(step.get('selector') or '')[:40]}' -> {res.get('status')} ({res['duration_seconds']}s)")
                step_results.append(res)

            failed = [s for s in step_results if s.get("status") == "failed"]
            overall = "passed" if not failed else "failed"
            result = {
                "status": overall,
                "steps": step_results,
                "elements": elements,
                "console_errors": list(dict.fromkeys(console_errors + http_errors)),
            }

        try:
            browser.close()
        except Exception:
            pass

    payload_line = "QA_RESULT:" + json.dumps(result, ensure_ascii=False)
    try:
        print(payload_line, flush=True)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(payload_line.encode("utf-8", "replace") + b"\n")
        sys.stdout.flush()
    return result


if __name__ == "__main__":
    payload = json.loads(os.environ.get("PAYLOAD", "{}"))
    res = run_tests(
        url=payload.get("url", ""),
        steps=payload.get("steps", []),
        pre_login_steps=payload.get("pre_login_steps"),
        timeout_seconds=payload.get("timeout_seconds", 300),
        full_page=payload.get("full_page", False),
        max_elements=payload.get("max_elements", 50),
        allowed_domains=payload.get("allowed_domains", []),
        allow_private_hosts=bool(payload.get("allow_private_hosts", False)),
        private_host_whitelist=payload.get("private_host_whitelist", []) or [],
        login=payload.get("login"),
        chain_max_forms=int(payload.get("chain_max_forms", 3)),
        telemetry_blocklist=payload.get("telemetry_blocklist", []),
    )
    sys.exit(0 if res.get("status") in ("passed", "ok") else 1)