"""Orchestrator for both scenario modes. Produces the structured result dict."""
import json
import logging
import random
import time
from urllib.parse import urljoin, urlparse

from app.agent.dispatcher import Dispatcher
from app.agent.guardian import Guardian
from app.agent.judge import Judge
from app.agent.keeper import Keeper
from app.agent.navigator import Navigator
from app.agent.sandbox import SandboxExecutor
from app.agent.scanner import extract_console_errors
from app.agent.strategist import Strategist
from app.config import settings

logger = logging.getLogger(__name__)


def _make_navigator() -> Navigator:
    executor = SandboxExecutor()
    return Navigator(executor)


def _steps_for_specific(script_text: str, dispatcher: Dispatcher) -> list[dict]:
    return dispatcher.parse(script_text)


def _attach_console_cases(steps: list[dict], console_errors: list[str]) -> list[dict]:
    """Represent serious browser-console errors (4xx/5xx, JS exceptions,
    failed requests) as extra failed test cases so the run counters and verdict
    reflect real page problems (e.g. a 401 despite all steps 'passing')."""
    serious = extract_console_errors(console_errors)
    if not serious:
        return steps

    covered: set[str] = set()
    out = [dict(s) for s in steps]
    # A step that "passed" but carries its own serious console error is a
    # contradiction (green verdict + red console error). Demote it to failed so
    # the verdict and the reported console problem stay consistent, and treat
    # its own errors as covered (no duplicate case).
    for s in out:
        own = [
            line.strip() for line in (s.get("console_error") or "").split("\n")
            if line.strip()
        ]
        if not own:
            continue
        own_serious = [l for l in own if l in serious]
        if own_serious and s.get("status") == "passed":
            s["status"] = "failed"
            s["message"] = f"{s.get('message') or ''} [ошибка консоли: {'; '.join(own_serious)}]".strip()
        if s.get("status") == "failed":
            covered.update(own)

    order = max((s.get("order") or 0) for s in out) if out else 0
    for line in serious:
        if line.strip() in covered:
            continue
        order += 1
        out.append({
            "order": order, "action": "console_check", "selector": "", "value": "",
            "expected": "Ошибок в консоли браузера нет",
            "status": "failed",
            "message": f"Обнаружена ошибка консоли: {line[:250]}",
            "console_error": line,
        })
    return out


def _finalize_status(steps: list[dict], base_status: str) -> str:
    if base_status in ("timeout", "error"):
        return base_status
    if not steps:
        return "error"
    failed = any(s.get("status") != "passed" for s in steps)
    return "failed" if failed else "passed"


def run_scenario(url: str, script_text: str, mode: str = "specific",
                 test_run_id: str = "", name: str = "", login: dict | None = None) -> dict:
    keeper = Keeper(test_run_id)
    dispatcher = Dispatcher()
    judge = Judge()
    navigator = _make_navigator()

    keeper.log("start", {"url": url, "mode": mode, "name": name})

    if mode == "full_page":
        return run_full_page(url, test_run_id=test_run_id, name=name,
                             keeper=keeper, navigator=navigator, login=login)

    steps = dispatcher.parse(script_text)
    if not steps:
        return {
            "status": "error",
            "error": "Не удалось разобрать сценарий на шаги. Опишите действия с глаголами: нажми, введи, проверь.",
            "steps": [],
            "console_errors": [],
            "audit": json.loads(keeper.dump()),
        }
    keeper.log("dispatched", {"step_count": len(steps)})
    return run_steps(url, steps, test_run_id=test_run_id, name=name,
                     keeper=keeper, navigator=navigator, login=login)


def run_steps(url: str, steps: list[dict], test_run_id: str = "", name: str = "",
              keeper: Keeper | None = None, navigator: Navigator | None = None,
              login: dict | None = None) -> dict:
    """Execute pre-built structured steps (used by saved suites)."""
    keeper = keeper or Keeper(test_run_id)
    navigator = navigator or _make_navigator()
    judge = Judge()

    timeout_minutes = 5
    timed_out = {"hit": False}

    def _on_timeout() -> None:
        timed_out["hit"] = True
        keeper.log("timeout", {"timeout_seconds": timeout_minutes * 60})

    guardian = Guardian(timeout_seconds=timeout_minutes * 60)
    guardian.start(on_timeout=_on_timeout)
    try:
        result = navigator.execute(url, steps, timeout_minutes, full_page=False, login=login)
    finally:
        guardian.cancel()

    if timed_out["hit"] or guardian.elapsed() >= timeout_minutes * 60:
        result["status"] = "timeout"
        result["error"] = result.get("error") or "Превышено время выполнения теста"

    keeper.log("executed", {"sandbox_status": result.get("status")})
    assembled = _assemble_result(result, steps, judge, keeper, url)
    assembled["steps"] = _attach_console_cases(
        assembled.get("steps", []), assembled.get("console_errors", []))
    assembled["status"] = _finalize_status(
        assembled.get("steps", []),
        "timeout" if result.get("status") == "timeout" else "ok")
    if result.get("status") == "error":
        assembled["status"] = "error"
    return assembled


def _sample_repetitive(elements: list[dict], key: str = "css") -> list[dict]:
    """Collapse the 'repeatable-property' explosion without losing coverage.

    Many pages render long lists of identical items (product cards, menu rows,
    gallery thumbnails ...). Testing every one duplicates the same check N times.
    We keep up to :threshold: items per identical css-class group and drop the
    rest, so a catalog with 30 cards still yields representative coverage
    instead of 30 near-identical cases.

    Only homogeneous, non-unique classes are collapsed. Empty classes and
    forms are left untouched (they carry distinct logic/fields).
    """
    threshold = getattr(settings, "SAMPLE_THRESHOLD", 4)
    sample = getattr(settings, "SAMPLE_COUNT", 2)
    if not key:
        return elements

    keep: list[dict] = []
    groups: dict[tuple, list[int]] = {}
    for idx, el in enumerate(elements):
        etype = el.get("type")
        if etype not in ("link", "button"):
            keep.append(el)
            continue
        cls = (el.get(key) or "").strip()
        if not cls:
            keep.append(el)
            continue
        groups.setdefault((etype, cls), []).append(idx)

    for (etype, cls), idxs in groups.items():
        if len(idxs) <= threshold:
            keep.extend(elements[i] for i in idxs)
            continue
        sample_idx = sorted(random.sample(idxs, min(sample, len(idxs))))
        keep.extend(elements[i] for i in sample_idx)
    return keep


def run_full_page(url: str, test_run_id: str = "", name: str = "",
                  keeper: Keeper | None = None, navigator: Navigator | None = None,
                  login: dict | None = None) -> dict:
    """Two-phase full-page test: pre-login scan of the start page (auth checks),
    then login + post-login scan of the authenticated page."""
    keeper = keeper or Keeper(test_run_id)
    navigator = navigator or _make_navigator()
    judge = Judge()
    strategist = Strategist()
    timeout_minutes = 10  # per phase
    scan_steps = [{"action": "scan", "selector": "", "value": "", "expected": ""}]
    all_steps: list[dict] = []
    console_errors: list[str] = []
    timed_out = {"hit": False}

    def _on_timeout():
        timed_out["hit"] = True
        keeper.log("timeout", {"timeout_seconds": timeout_minutes * 60})

    # ---- Phase PRE-LOGIN: scan start page without credentials ----
    if login and login.get("username"):
        try:
            pre_scan = navigator.execute(url, scan_steps, timeout_minutes, full_page=True, login=None)
            pre_elements = pre_scan.get("elements", [])
            if pre_elements:
                pre_plan = strategist.generate_plan(url, _sample_repetitive(pre_elements))
                pre_steps = [s for case in pre_plan for s in case.get("steps", [])]
                if pre_steps:
                    pre_res = navigator.execute(url, pre_steps, timeout_minutes, full_page=True, login=None)
                    pre_assembled = _assemble_result(pre_res, pre_steps, judge, keeper, url)
                    all_steps.extend(pre_assembled.get("steps", []))
                    console_errors.extend(pre_assembled.get("console_errors", []))
        except Exception as exc:
            logger.exception("pre_login phase failed for %s", url)

    # ---- Phase POST-LOGIN: login + scan authenticated page ----
    guardian = Guardian(timeout_seconds=timeout_minutes * 60)
    guardian.start(on_timeout=_on_timeout)
    try:
        post_scan = navigator.execute(url, scan_steps, timeout_minutes, full_page=True, login=login)
    finally:
        guardian.cancel()

    elements = post_scan.get("elements", [])
    console_errors.extend(extract_console_errors(post_scan.get("console_errors", "")))
    keeper.log("post_scanned", {"element_count": len(elements), "pre_steps": len(all_steps)})

    if not elements and not all_steps:
        return {
            "status": "error",
            "error": "На странице не найдено интерактивных элементов",
            "steps": all_steps,
            "elements": [],
            "console_errors": console_errors,
            "audit": json.loads(keeper.dump()),
        }

    plan = strategist.generate_plan(url, _sample_repetitive(elements))
    plan_steps = [s for case in plan for s in case.get("steps", [])]
    if not plan_steps:
        plan_steps = [{"action": "click", "selector": e.get("selector", ""), "value": "", "expected": ""}
                      for e in elements[:settings.FULL_PAGE_MAX_ELEMENTS]]
    keeper.log("post_planned", {"cases": len(plan), "steps": len(plan_steps)})

    guardian = Guardian(timeout_seconds=timeout_minutes * 60)
    guardian.start(on_timeout=_on_timeout)
    try:
        result = navigator.execute(url, plan_steps, timeout_minutes, full_page=True, login=login)
    finally:
        guardian.cancel()

    if timed_out["hit"] or guardian.elapsed() >= timeout_minutes * 60:
        result["status"] = "timeout"
        result["error"] = result.get("error") or "Превышено время выполнения теста"

    result["elements"] = elements
    result.setdefault("console_errors", console_errors)
    assembled = _assemble_result(result, plan_steps, judge, keeper, url)
    all_steps.extend(assembled.get("steps", []))
    all_console = list(dict.fromkeys(
        console_errors + assembled.get("console_errors", [])))
    # Console errors not yet surfaced by any step become extra failed cases
    # (e.g. a 401 in the console must fail the run despite green checks).
    all_steps = _attach_console_cases(all_steps, all_console)
    # Sequential numbering across pre- + post-login phases
    for i, s in enumerate(all_steps, 1):
        s["order"] = i
    base = "timeout" if timed_out["hit"] else "ok"
    if assembled.get("status") == "error":
        base = "error"
    return {
        "status": _finalize_status(all_steps, base),
        "steps": all_steps,
        "console_errors": all_console,
        "elements": elements,
        "url": url,
        "audit": json.loads(keeper.dump()),
        "error": result.get("error", ""),
    }


def _assemble_result(result: dict, steps: list[dict], judge: Judge,
                     keeper: Keeper, url: str) -> dict:
    step_results = []
    raw_steps = result.get("steps", [])
    order = 0

    # The sandbox prepends a synthetic login case ("check_auth") as the very
    # first step result whenever a full-page phase authenticated successfully.
    # Pop it so plan steps map 1:1 against the remaining outcomes, and emit it
    # as its own visible test case (counters/verdict must include the login).
    login_case = None
    if raw_steps and raw_steps[0].get("action") == "check_auth" and \
            "Вход с корректными" in (raw_steps[0].get("expected") or ""):
        login_case = raw_steps.pop(0)
    if login_case:
        lv = judge.evaluate_step(
            {"action": "check_auth",
             "expected": login_case.get("expected", "Вход с корректными учётными данными выполнен успешно")},
            login_case)
        order += 1
        lv["order"] = order
        lv["action"] = "check_auth"
        lv["selector"] = ""
        lv["value"] = ""
        lv["expected"] = login_case.get("expected") or "Вход с корректными учётными данными выполнен успешно"
        lv["console_error"] = login_case.get("console_error", "")
        if login_case.get("url"):
            lv["url"] = login_case["url"]
        step_results.append(lv)
        keeper.log("step_result", {"order": order, "status": lv["status"]})

    for i, step in enumerate(steps):
        outcome = raw_steps[i] if i < len(raw_steps) else {"status": "failed", "message": "Шаг не выполнен"}
        verdict = judge.evaluate_step(step, outcome)
        order += 1
        verdict["action"] = step.get("action", "")
        verdict["selector"] = step.get("selector", "")
        verdict["value"] = step.get("value", "")
        verdict["expected"] = step.get("expected", "")
        verdict["order"] = order
        verdict["console_error"] = outcome.get("console_error", "")
        if outcome.get("url"):
            verdict["url"] = outcome["url"]
        if outcome.get("screenshot"):
            verdict["screenshot"] = outcome["screenshot"]
        if outcome.get("form_data"):
            verdict["form_data"] = outcome["form_data"]
        if outcome.get("page"):
            verdict["page"] = outcome["page"]
        step_results.append(verdict)
        keeper.log("step_result", {"order": order, "status": verdict["status"]})

        # Chained cases discovered inside a click (revealed modal/form).
        for c in outcome.get("chain") or []:
            synth = {
                "action": "check_form",
                "selector": "",
                "value": "",
                "expected": "Цепочка: появившаяся после клика форма заполняется и отправляется",
                "expected_effect": "state_changed",
            }
            cv = judge.evaluate_step(synth, c)
            order += 1
            cv["order"] = order
            cv["action"] = c.get("action") or "fill_form"
            cv["selector"] = ""
            cv["value"] = ""
            cv["expected"] = synth["expected"]
            cv["console_error"] = c.get("console_error", "")
            if c.get("screenshot"):
                cv["screenshot"] = c["screenshot"]
            if c.get("form_data"):
                cv["form_data"] = c["form_data"]
            step_results.append(cv)
            keeper.log("chain_result", {"order": order, "status": cv["status"]})

    console_errors = list(dict.fromkeys(
        list(result.get("console_errors", [])) +
        [s.get("console_error", "") for s in step_results if s.get("console_error")]
    ))
    # Serious browser-console errors are attached globally once per run by the
    # top-level runners (_attach_console_cases) to avoid cross-phase duplicates.
    overall = result.get("status")
    if overall not in ("timeout", "error"):
        overall = _finalize_status(step_results, overall)

    return {
        "status": overall,
        "steps": step_results,
        "console_errors": console_errors,
        "error": result.get("error", ""),
        "url": url,
        "elements": result.get("elements", []),
        "audit": json.loads(keeper.dump()),
    }


def run_site_crawl(url: str, test_run_id: str = "", name: str = "",
                   keeper: Keeper | None = None, navigator: Navigator | None = None,
                   login: dict | None = None) -> dict:
    """Site-wide mode: BFS over same-host internal pages; scan + generated plan
    per page. Each page runs in its own sandbox invocation (fresh login inside)."""
    keeper = keeper or Keeper(test_run_id)
    navigator = navigator or _make_navigator()
    judge = Judge()
    strategist = Strategist()

    start_host = urlparse(url).netloc

    def normalize(raw: str, base: str) -> str | None:
        u = urljoin(base, (raw or "").strip())
        p = urlparse(u)
        if p.scheme not in ("http", "https"):
            return None
        if p.netloc != start_host:
            return None
        path = (p.path or "/").rstrip("/") or "/"
        # ignore query/fragment for dedupe to avoid combinatorial explosion
        return f"{p.scheme}://{p.netloc}{path}"

    start_norm = normalize(url, url) or url
    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(start_norm, 0)]
    all_steps: list[dict] = []
    pages: list[str] = []
    console_errors: list[str] = []
    timeout_minutes = 10
    deadline = time.time() + settings.CRAWL_TIMEOUT_MINUTES * 60

    while queue and len(visited) < settings.CRAWL_MAX_PAGES and time.time() < deadline:
        cur, depth = queue.pop(0)
        if not cur or cur in visited:
            continue
        visited.add(cur)
        pages.append(cur)
        keeper.log("crawl_page", {"url": cur, "depth": depth})

        # Two-phase testing per page, per testing methodology:
        #   Phase "guest"  — scan, plan and execute the page anonymously.
        #   Phase "auth"   — same, but logged in (only when creds are set).
        # So public pages are fully checked twice (as visitor and as user) and
        # the login/closed-zone flows get their own checks without credentials.
        phases = [{"name": "guest", "login": None}]
        if login and login.get("username"):
            phases.append({"name": "auth", "login": login})

        # Link discovery runs once against the fullest session (authed if present)
        # so private pages are discovered too; same-host URLs are enqueued.
        disc_login = login if (login and login.get("username")) else None
        discovered_some = False
        page_concat: list[dict] = []

        for phase in phases:
            try:
                scan_steps = [{"action": "scan", "selector": "", "value": "", "expected": ""}]
                sres = navigator.execute(cur, scan_steps, timeout_minutes,
                                         full_page=True, login=phase["login"])
                elements = sres.get("elements", [])
                keeper.log("crawl_scanned", {"url": cur, "phase": phase["name"],
                                             "elements": len(elements)})
            except Exception as exc:
                logger.exception("crawl scan failed for %s (%s)", cur, phase["name"])
                all_steps.append({"order": len(all_steps) + 1, "action": "goto", "selector": "",
                                  "value": cur,
                                  "expected": f"Страница {cur} открывается ({phase['name']})",
                                  "status": "failed", "message": str(exc)[:200], "page": cur})
                continue

            if depth < settings.CRAWL_DEPTH and phase["login"] == disc_login \
                    and not discovered_some:
                # 1) Fast path: href-based discovery from the static scan.
                for e in elements:
                    if e.get("type") != "link":
                        continue
                    nxt = normalize(e.get("href"), cur)
                    if nxt and nxt not in visited and all(nxt != q for q, _ in queue):
                        queue.append((nxt, depth + 1))
                # 2) Robust path: click-based discovery — handles SPAs where links
                #    use href="#" / JS navigation (e.g. saucedemo cart & products).
                try:
                    disc_steps = [{"action": "discover_links", "selector": "",
                                   "value": "", "expected": ""}]
                    dres = navigator.execute(cur, disc_steps, timeout_minutes,
                                             full_page=True, login=disc_login)
                    disc = []
                    for s in (dres.get("steps") or []):
                        disc = s.get("discovered", [])
                        if disc:
                            break
                    for item in disc:
                        nxt = normalize(item.get("url"), cur)
                        if nxt and nxt not in visited and all(nxt != q for q, _ in queue):
                            queue.append((nxt, depth + 1))
                except Exception as exc:
                    logger.exception("crawl link discovery failed for %s", cur)
                discovered_some = True

            plan = strategist.generate_plan(cur, _sample_repetitive(elements))
            plan_steps = [s for case in plan for s in case.get("steps", [])]
            if not plan_steps:
                continue

            pre = [s for s in plan_steps if s.get("action") == "check_auth"]
            main = [s for s in plan_steps if s.get("action") != "check_auth"]

            if pre:
                # Invalid-credentials check always runs anonymously: the form
                # must reject wrong/incomplete credentials regardless of phase.
                try:
                    pre_res = navigator.execute(cur, pre, timeout_minutes,
                                                full_page=True, login=None)
                    pre_assembled = _assemble_result(pre_res, pre, judge, keeper, cur)
                    for v in pre_assembled.get("steps", []):
                        v["page"] = cur
                        v["order"] = len(page_concat) + 1
                        page_concat.append(v)
                except Exception as exc:
                    logger.exception("crawl check_auth failed for %s", cur)

            if main:
                try:
                    pres = navigator.execute(cur, main, timeout_minutes,
                                             full_page=True, login=phase["login"])
                except Exception as exc:
                    logger.exception("crawl plan failed for %s (%s)", cur, phase["name"])
                    page_concat.append({"order": len(page_concat) + 1,
                                        "action": "check_form", "selector": "", "value": cur,
                                        "expected": f"Проверки страницы {cur} выполняются ({phase['name']})",
                                        "status": "failed", "message": str(exc)[:200], "page": cur})
                    continue

                assembled = _assemble_result(pres, main, judge, keeper, cur)
                for v in assembled.get("steps", []):
                    v["page"] = cur
                    v["order"] = len(page_concat) + 1
                    page_concat.append(v)
                    # Transitions performed during step execution (form submit,
                    # JS navigation) land on pages href-discovery cannot see —
                    # enqueue them so they get tested too.
                    nxt = normalize(v.get("url"), cur)
                    if nxt and nxt != cur and nxt not in visited and all(nxt != q for q, _ in queue):
                        queue.append((nxt, depth + 1))
                console_errors.extend(assembled.get("console_errors", []))
                page_err = (pres or {}).get("error", "") or assembled.get("error", "")
                if page_err:
                    page_concat.append({
                        "order": len(page_concat) + 1, "action": "goto", "selector": "", "value": cur,
                        "expected": f"Страница {cur} тестируется без ошибок песочницы ({phase['name']})",
                        "status": "failed",
                        "message": f"Ошибка песочницы на {cur}: {str(page_err)[:300]}",
                        "page": cur,
                    })
                    console_errors.append(f"{cur}: {str(page_err)[:200]}")
                if assembled.get("status") == "timeout":
                    keeper.log("crawl_timeout", {"url": cur, "phase": phase["name"]})
                    break

        all_steps.extend(page_concat)

    all_steps = _attach_console_cases(all_steps, list(dict.fromkeys(console_errors)))
    for i, s in enumerate(all_steps, 1):
        s["order"] = i
    failed = sum(1 for s in all_steps if s.get("status") == "failed")
    overall = _finalize_status(all_steps, "ok")
    err_summary = "; ".join(f"{p}: ошибка фазы" for p in pages)[:400] if failed == len(all_steps) and all_steps else ""
    return {
        "status": overall,
        "steps": all_steps,
        "pages": pages,
        "console_errors": list(dict.fromkeys(console_errors)),
        "error": "" if all_steps and failed < len(all_steps) else
                 (err_summary or ("Не удалось просканировать ни одной страницы" if not all_steps else "")),
        "url": url,
        "elements": [],
        "audit": json.loads(keeper.dump()),
    }