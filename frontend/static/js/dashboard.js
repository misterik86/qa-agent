const API = window.location.origin;
let accessToken = localStorage.getItem("qa_access") || "";
let refreshToken = localStorage.getItem("qa_refresh") || "";

function apiHeaders(extra = {}) {
  return { "Content-Type": "application/json", ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}), ...extra };
}

async function api(path, method = "GET", body = null, headers = {}) {
  const resp = await fetch(`${API}${path}`, {
    method,
    headers: apiHeaders(headers),
    body: body ? JSON.stringify(body) : null,
  });
  if (resp.status === 401 && refreshToken) {
    const ok = await tryRefresh();
    if (ok) return api(path, method, body, headers);
  }
  let data = null;
  try { data = await resp.json(); } catch (e) { data = null; }
  if (!resp.ok) throw new Error(data?.detail || `HTTP ${resp.status}`);
  return data;
}

async function tryRefresh() {
  try {
    const resp = await fetch(`${API}/api/auth/refresh`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!resp.ok) return false;
    const data = await resp.json();
    accessToken = data.access_token;
    refreshToken = data.refresh_token;
    localStorage.setItem("qa_access", accessToken);
    localStorage.setItem("qa_refresh", refreshToken);
    return true;
  } catch (e) { return false; }
}

function showAlert(msg, type = "danger") {
  const area = document.getElementById("alert-area");
  area.innerHTML = `<div class="alert alert-${type} alert-dismissible fade show">${msg}
    <button class="btn-close" data-bs-dismiss="alert"></button></div>`;
  setTimeout(() => { area.innerHTML = ""; }, 8000);
}

function setAuthUI(loggedIn) {
  document.getElementById("auth-view").classList.toggle("d-none", loggedIn);
  document.getElementById("app-view").classList.toggle("d-none", !loggedIn);
}

// ---------- Auth ----------
document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const data = await api("/api/auth/login", "POST", {
      email: document.getElementById("login-email").value,
      password: document.getElementById("login-pass").value,
    });
    accessToken = data.access_token; refreshToken = data.refresh_token;
    localStorage.setItem("qa_access", accessToken);
    localStorage.setItem("qa_refresh", refreshToken);
    loadApp();
  } catch (err) { showAlert(err.message); }
});

document.getElementById("register-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/auth/register", "POST", {
      email: document.getElementById("reg-email").value,
      password: document.getElementById("reg-pass").value,
      full_name: document.getElementById("reg-name").value,
      accepted_tos: document.getElementById("reg-tos").checked,
      accepted_privacy: document.getElementById("reg-privacy").checked,
    });
    showAlert("Регистрация успешна! Проверьте email для подтверждения (в локальном режиме письмо в MailHog).", "success");
  } catch (err) { showAlert(err.message); }
});

document.getElementById("forgot-link").addEventListener("click", (e) => {
  e.preventDefault();
  document.getElementById("login-form").classList.add("d-none");
  document.getElementById("forgot-form").classList.remove("d-none");
});
document.getElementById("back-to-login").addEventListener("click", (e) => {
  e.preventDefault();
  document.getElementById("forgot-form").classList.add("d-none");
  document.getElementById("login-form").classList.remove("d-none");
});
document.getElementById("back-to-login2").addEventListener("click", (e) => {
  e.preventDefault();
  document.getElementById("reset-form").classList.add("d-none");
  document.getElementById("login-form").classList.remove("d-none");
});
document.getElementById("reset-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/auth/reset-password", "POST", {
      token: document.getElementById("reset-token").value,
      new_password: document.getElementById("reset-pass").value,
    });
    document.getElementById("reset-form").classList.add("d-none");
    document.getElementById("login-form").classList.remove("d-none");
    showAlert("Пароль изменён. Войдите с новым паролем.", "success");
  } catch (err) { showAlert(err.message); }
});
document.getElementById("forgot-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/auth/forgot-password", "POST", { email: document.getElementById("forgot-email").value });
    showAlert("Ссылка для сброса отправлена (в локальном режиме — в MailHog).", "success");
  } catch (err) { showAlert(err.message); }
});

document.getElementById("logout-btn").addEventListener("click", async () => {
  try { await api("/api/auth/logout", "POST", { refresh_token: refreshToken }); } catch (e) {}
  accessToken = ""; refreshToken = "";
  localStorage.removeItem("qa_access"); localStorage.removeItem("qa_refresh");
  setAuthUI(false);
});

// ---------- App ----------
async function loadApp() {
  setAuthUI(true);
  await Promise.all([loadStats(), loadRuns(), loadScenarios(), loadSchedule(), loadWebhooks(), loadSubscription(), loadKeys(), loadSuites(), loadCredentials()]);
}

async function loadStats() {
  try {
    const s = await api("/api/dashboard/stats");
    document.getElementById("stat-total").textContent = s.total_runs;
    document.getElementById("stat-passed").textContent = s.passed;
    document.getElementById("stat-failed").textContent = s.failed;
    document.getElementById("stat-limit").textContent = s.usage_percent === null ? "∞" : s.usage_percent + "%";
    document.getElementById("nav-plan").textContent = `Тариф: ${s.plan} | Тесты: ${s.tests_used}`;
  } catch (e) {}
}

async function loadRuns() {
  try {
    const runs = await api("/api/runs");
    const tbody = document.querySelector("#runs-table tbody");
    tbody.innerHTML = "";
    runs.forEach((r) => {
      const statusBadge = { passed: "success", failed: "danger", running: "info", queued: "secondary", timeout: "warning", cancelled: "secondary", limit_exceeded: "warning", error: "danger" }[r.status] || "secondary";
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${esc(r.name)}</td><td><small>${esc(r.url)}</small></td>
        <td><small>${{ full_page: "Текущая страница", site: "Весь сайт", specific: "Сценарий" }[r.mode] || r.mode}</small></td>
        <td><span class="badge bg-${statusBadge}">${esc(r.status)}</span></td>
        <td><span class="text-success">${r.passed_steps}P</span> / <span class="text-danger">${r.failed_steps}F</span></td>
        <td><small>${r.duration_seconds}s</small></td>
        <td><a href="#" class="btn btn-sm btn-outline-primary view-run" data-id="${r.id}">Детали</a></td>`;
      tbody.appendChild(tr);
    });
    document.querySelectorAll(".view-run").forEach((b) => b.addEventListener("click", async (e) => {
      e.preventDefault();
      openRunModal(b.dataset.id);
    }));
  } catch (e) { showAlert(e.message); }
}

async function openRunModal(runId) {
  try {
    const r = await api(`/api/runs/${runId}`);
    const DELTA_BADGE = { regressed: "danger", fixed: "success", new: "info" };
    const DELTA_LABEL = { regressed: "упало", fixed: "починено", new: "новый" };
    const stepsHtml = r.steps.map((s) => {
      const tc = s.expected || `${s.action}${s.selector ? " «" + s.selector + "»" : ""}`;
      const shot = s.screenshot
        ? `<a href="#" class="run-shot" data-src="${s.screenshot}">[скриншот]</a>` : "";
      const delta = s.delta ? ` <span class="badge bg-${DELTA_BADGE[s.delta]}">${DELTA_LABEL[s.delta]}</span>` : "";
      const pageTag = s.page ? `<div class="small text-muted" style="font-size:.75em">${esc(s.page)}</div>` : "";
      return `
      <tr>
        <td>${s.order}</td>
        <td class="tc-cell">${esc(tc)}${delta}${pageTag}</td>
        <td title="${esc(s.selector || "")}">${esc(s.action_text || s.action)}</td>
        <td>${esc(s.message || "")} ${shot}</td>
        <td><span class="badge bg-${s.status === "passed" ? "success" : "danger"}">${esc(s.status)}</span></td>
        ${s.console_error ? `<td class="console-error">${esc(s.console_error)}</td>` : "<td></td>"}
      </tr>`;
    }).join("");
    const errBlock = r.error_message
      ? `<div class="alert alert-danger py-2 mb-3"><strong>Ошибка прогона:</strong> ${esc(r.error_message)}</div>` : "";
    let deltaBlock = "";
    if (r.delta) {
      const d = r.delta;
      const cls = d.regressed.length ? "alert-danger" : (d.fixed.length ? "alert-success" : "alert-secondary");
      deltaBlock = `<div class="alert ${cls} py-2 mb-3"><strong>Сравнение с прошлым прогоном:</strong> ${esc(d.summary)}
        ${(d.regressed.length ? `<div class="small mt-1">Новые провалы: <ul class="mb-0">${d.regressed.map((t) => `<li>${esc(t)}</li>`).join("")}</ul></div>` : "")}
        ${(d.fixed.length ? `<div class="small mt-1">Починено: ${d.fixed.map(esc).join("; ")}</div>` : "")}
      </div>`;
    }
    const suiteBtn = r.suite_id ? "" :
      `<button class="btn btn-sm btn-outline-success" id="save-as-suite-btn">Сохранить как набор</button>`;
    const html = `
      <div class="modal fade" tabindex="-1"><div class="modal-dialog modal-xl"><div class="modal-content">
        <div class="modal-header"><h5 class="modal-title">${esc(r.name)} — ${esc(r.status_label)}</h5>
        <button class="btn-close" data-bs-dismiss="modal"></button></div>
        <div class="modal-body">
          <p><strong>URL:</strong> <a href="${esc(r.url)}" target="_blank">${esc(r.url)}</a>
          &nbsp;|&nbsp; <strong>Время:</strong> ${r.total_time_seconds}s &nbsp;|&nbsp;
          <strong>Всего шагов:</strong> ${r.total_steps} &nbsp;|&nbsp;
          <strong>PASS:</strong> ${r.passed_steps} <strong>FAIL:</strong> ${r.failed_steps}</p>
          ${errBlock}
          ${deltaBlock}
          <table class="table table-sm table-striped"><thead><tr><th>#</th><th>Тест-кейс</th><th>Действие</th><th>Фактический результат</th><th>Вердикт</th><th>Ошибка консоли</th></tr></thead>
          <tbody>${stepsHtml}</tbody></table>
          <div class="mt-2 d-flex gap-2 flex-wrap">
            <button class="btn btn-sm btn-outline-primary" onclick="downloadReport('${r.id}','json')">JSON</button>
            <button class="btn btn-sm btn-outline-primary" onclick="downloadReport('${r.id}','html')">HTML</button>
            <button class="btn btn-sm btn-outline-primary" onclick="downloadReport('${r.id}','pdf')">PDF</button>
            <button class="btn btn-sm btn-outline-primary" onclick="downloadReport('${r.id}','junit')">JUnit</button>
            <button class="btn btn-sm btn-outline-danger" id="bugreport-btn">Баг-репорт (.md)</button>
            <button class="btn btn-sm btn-outline-secondary" id="share-btn">Публичная ссылка</button>
            ${suiteBtn}
          </div>
        </div></div></div></div>`;
    document.body.insertAdjacentHTML("beforeend", html);
    const modal = document.body.lastElementChild;
    new bootstrap.Modal(modal).show();
    modal.querySelectorAll(".run-shot").forEach((a) => a.addEventListener("click", (e) => {
      e.preventDefault();
      const w = window.open("", "_blank");
      if (w) w.document.write(`<img src="${a.dataset.src}" style="max-width:100%">`);
    }));
    modal.querySelector("#bugreport-btn").addEventListener("click", () =>
      downloadFile(`/api/runs/${r.id}/bugreport`, `bugreport_${r.id}.md`));
    modal.querySelector("#share-btn").addEventListener("click", async () => {
      try {
        const sh = await api(`/api/runs/${r.id}/share`, "POST");
        const url = `${location.origin}/report/${sh.token}`;
        try { await navigator.clipboard.writeText(url); showAlert("Ссылка скопирована: " + url, "success"); }
        catch (e) { prompt("Публичная ссылка на отчёт:", url); }
      } catch (e) { showAlert(e.message); }
    });
    const saveBtn = modal.querySelector("#save-as-suite-btn");
    if (saveBtn) saveBtn.addEventListener("click", async () => {
      const name = prompt("Название набора:", r.name);
      if (!name) return;
      try {
        await api(`/api/runs/${r.id}/save-as-suite`, "POST", { name });
        showAlert("Набор сохранён. Раздел «Наборы».", "success");
      } catch (e) { showAlert(e.message); }
    });
    modal.addEventListener("hidden.bs.modal", () => modal.remove());
  } catch (e) { showAlert(e.message); }
}

async function downloadFile(path, filename) {
  try {
    const resp = await fetch(API + path, {
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (e) { showAlert(e.message); }
}

function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function downloadReport(runId, fmt) {
  try {
    const resp = await fetch(`${API}/api/runs/${runId}/report/${fmt}`, {
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
    });
    if (resp.status === 401 && refreshToken) {
      const ok = await tryRefresh();
      if (ok) return downloadReport(runId, fmt);
    }
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try { detail = (await resp.json()).detail || detail; } catch (e) {}
      throw new Error(detail);
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `report_${runId}.${fmt}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (e) { showAlert(e.message); }
}

// ---------- Run test ----------
document.getElementById("run-mode").addEventListener("change", () => {
  const mode = document.getElementById("run-mode").value;
  document.getElementById("script-block").classList.toggle("d-none", mode !== "specific");
});

document.getElementById("validate-btn").addEventListener("click", async () => {
  const text = document.getElementById("run-script").value;
  const el = document.getElementById("validation-result");
  if (!text.trim()) { el.innerHTML = '<div class="text-warning">Введите текст сценария</div>'; return; }
  el.innerHTML = '<div class="text-muted">Проверка...</div>';
  try {
    const v = await api("/api/scenarios/validate", "POST", { script_text: text });
    if (v.valid) el.innerHTML = '<div class="text-success">✓ Сценарий валиден</div>';
    else {
      el.innerHTML = `<div class="text-danger"><strong>Сценарий невалиден:</strong><ul>${v.errors.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
        ${v.suggestions.length ? `<div class="text-info">Рекомендации: ${v.suggestions.map(esc).join("; ")}</div>` : ""}</div>`;
    }
  } catch (e) { el.innerHTML = `<div class="text-danger">${esc(e.message)}</div>`; }
});

document.getElementById("run-btn").addEventListener("click", async () => {
  const url = document.getElementById("run-url").value;
  const name = document.getElementById("run-name").value || "Тест " + new Date().toLocaleString();
  const mode = document.getElementById("run-mode").value;
  const script = document.getElementById("run-script").value;
  const scenarioId = document.getElementById("run-scenario").value || null;
  const credentialId = document.getElementById("run-cred").value || null;
  const consent = document.getElementById("run-consent").checked;
  if (!url) { showAlert("Укажите URL"); return; }
  if (!consent) { showAlert("Подтвердите, что имеете право тестировать сайт"); return; }
  try {
    const run = await api("/api/runs", "POST", { url, name, mode, script_text: script, scenario_id: scenarioId, credential_id: credentialId, consent });
    showAlert(`Тест запущен: ${run.name}`, "success");
    startWsTimer(run.id);
    await loadRuns();
  } catch (e) { showAlert(e.message); }
});

function startWsTimer(runId) {
  const el = document.getElementById("ws-status");
  let proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws/time/${runId}`);
  ws.onmessage = (ev) => {
    const d = JSON.parse(ev.data);
    el.textContent = `⏱ elapsed ${d.elapsed}s | прогноз ${d.predicted}s | статус: ${d.status}`;
    if (["passed", "failed", "timeout", "error", "cancelled"].includes(d.status)) {
      el.textContent = `Завершено (${d.status})`;
      ws.close();
      loadRuns();
    }
  };
  ws.onerror = () => {};
}

// ---------- Scenarios ----------
async function loadScenarios() {
  try {
    const scenarios = await api("/api/scenarios");
    const tbody = document.querySelector("#scenarios-table tbody");
    const sel = document.getElementById("run-scenario");
    const sch = document.getElementById("sch-scenario");
    tbody.innerHTML = ""; sel.innerHTML = '<option value="">— без сценария —</option>'; sch.innerHTML = "";
    scenarios.forEach((s) => {
      const badges = s.tags.map((t) => `<span class="badge bg-secondary me-1">${esc(t)}</span>`).join("");
      const statusBadge = { draft: "secondary", active: "success", deprecated: "warning", failed: "danger" }[s.status] || "secondary";
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${esc(s.name)}</td><td><small>${esc(s.url)}</small></td>
        <td><span class="badge bg-${statusBadge}">${esc(s.status)}</span></td>
        <td>${badges}</td><td>v${s.current_version}</td>
        <td>
          <a href="#" class="btn btn-sm btn-outline-success run-sc" data-id="${s.id}">Запустить</a>
          <a href="#" class="btn btn-sm btn-outline-warning dep-sc" data-id="${s.id}">Deprecated</a>
          <a href="#" class="btn btn-sm btn-outline-danger del-sc" data-id="${s.id}">Удалить</a>
        </td>`;
      tbody.appendChild(tr);
      sel.innerHTML += `<option value="${s.id}">${esc(s.name)}</option>`;
      sch.innerHTML += `<option value="${s.id}">${esc(s.name)}</option>`;
    });
    document.querySelectorAll(".run-sc").forEach((b) => b.addEventListener("click", async (e) => {
      e.preventDefault();
      try {
        const run = await api("/api/runs", "POST", { scenario_id: b.dataset.id, consent: true, url: "x", name: "" });
        showAlert("Тест запущен", "success");
        startWsTimer(run.id);
        loadRuns();
      } catch (err) { showAlert(err.message); }
    }));
    document.querySelectorAll(".del-sc").forEach((b) => b.addEventListener("click", async (e) => {
      e.preventDefault();
      if (!confirm("Удалить сценарий?")) return;
      try { await api(`/api/scenarios/${b.dataset.id}`, "DELETE"); loadScenarios(); } catch (err) { showAlert(err.message); }
    }));
    document.querySelectorAll(".dep-sc").forEach((b) => b.addEventListener("click", async (e) => {
      e.preventDefault();
      const comment = prompt("Комментарий (обязателен для перевода в Deprecated):");
      if (!comment) return;
      try {
        await api(`/api/scenarios/${b.dataset.id}`, "PUT", { status: "deprecated", change_comment: comment });
        loadScenarios();
      } catch (err) { showAlert(err.message); }
    }));
  } catch (e) { showAlert(e.message); }
}

document.getElementById("sc-create").addEventListener("click", async () => {
  const tplChecked = document.getElementById("sc-tpl").checked;
  let script = document.getElementById("sc-script").value;
  if (tplChecked) {
    script = 'Нажми на кнопку "Войти", введи в поле логин test@test.com, введи в поле пароль 123456, нажми "Отправить", проверь, что появилось сообщение "Добро пожаловать"';
    document.getElementById("sc-script").value = script;
  }
  try {
    await api("/api/scenarios", "POST", {
      name: document.getElementById("sc-name").value,
      url: document.getElementById("sc-url").value,
      mode: document.getElementById("sc-mode").value,
      script_text: script,
      tags: document.getElementById("sc-tags").value.split(",").map((t) => t.trim()).filter(Boolean),
    });
    showAlert("Сценарий сохранён", "success");
    loadScenarios();
  } catch (e) { showAlert(e.message); }
});

// ---------- Schedule ----------
document.getElementById("sch-create").addEventListener("click", async () => {
  try {
    const target = document.getElementById("sch-target").value;
    const channel = document.getElementById("sch-channel").value;
    const body = {
      scenario_id: document.getElementById("sch-scenario").value,
      frequency: document.getElementById("sch-freq").value,
      channel,
    };
    if (channel === "telegram") body.chat_id = target;
    else if (channel === "vk") { body.vk_user_id = target; body.vk_token = target; }
    else body.email_recipient = target;
    await api("/api/schedules", "POST", body);
    showAlert("Расписание добавлено", "success");
    loadSchedule();
  } catch (e) { showAlert(e.message); }
});

async function loadSchedule() {
  try {
    const jobs = await api("/api/schedules");
    const tbody = document.querySelector("#schedule-table tbody");
    tbody.innerHTML = "";
    jobs.forEach((j) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${esc(j.scenario_id)}</td><td>${esc(j.frequency)}</td><td>${esc(j.channel)}</td>
        <td>${esc(j.chat_id || j.vk_user_id || j.email_recipient || "-")}</td>
        <td><span class="badge bg-${j.status === "active" ? "success" : "warning"}">${esc(j.status)}</span></td>
        <td>${j.last_run_at ? new Date(j.last_run_at).toLocaleString() : "—"}</td>
        <td>
          ${j.status === "active"
            ? `<a href="#" class="btn btn-sm btn-outline-secondary pause-sch" data-id="${j.id}">Пауза</a>`
            : `<a href="#" class="btn btn-sm btn-outline-success resume-sch" data-id="${j.id}">Возобновить</a>`}
          <a href="#" class="btn btn-sm btn-outline-danger del-sch" data-id="${j.id}">Удалить</a>
        </td>`;
      tbody.appendChild(tr);
    });
    document.querySelectorAll(".pause-sch").forEach((b) => b.addEventListener("click", async (e) => {
      e.preventDefault(); await api(`/api/schedules/${b.dataset.id}/pause`, "POST"); loadSchedule();
    }));
    document.querySelectorAll(".resume-sch").forEach((b) => b.addEventListener("click", async (e) => {
      e.preventDefault(); await api(`/api/schedules/${b.dataset.id}/resume`, "POST"); loadSchedule();
    }));
    document.querySelectorAll(".del-sch").forEach((b) => b.addEventListener("click", async (e) => {
      e.preventDefault(); await api(`/api/schedules/${b.dataset.id}`, "DELETE"); loadSchedule();
    }));
  } catch (e) {}
}

// ---------- Settings ----------
document.getElementById("cp-btn").addEventListener("click", async () => {
  try {
    await api("/api/auth/change-password", "POST", {
      old_password: document.getElementById("cp-old").value,
      new_password: document.getElementById("cp-new").value,
    });
    showAlert("Пароль изменён", "success");
  } catch (e) { showAlert(e.message); }
});

document.getElementById("api-key-btn").addEventListener("click", async () => {
  try {
    const data = await api("/api/keys", "POST", { name: "ci" });
    document.getElementById("api-key-out").value = data.key;
    navigator.clipboard.writeText(data.key);
    showAlert("API-ключ создан и скопирован", "success");
  } catch (e) { showAlert(e.message); }
});

async function loadKeys() {
  try {
    const keys = await api("/api/keys");
    if (keys.length) document.getElementById("api-key-out").placeholder = `Ключей: ${keys.length} (созданные ключи не показываются повторно)`;
  } catch (e) {}
}

document.getElementById("wh-add").addEventListener("click", async () => {
  try {
    await api("/api/webhooks", "POST", {
      url: document.getElementById("wh-url").value,
      secret: document.getElementById("wh-secret").value,
    });
    showAlert("Вебхук добавлен", "success");
    loadWebhooks();
  } catch (e) { showAlert(e.message); }
});

async function loadWebhooks() {
  try {
    const hooks = await api("/api/webhooks");
    const ul = document.getElementById("wh-list");
    ul.innerHTML = "";
    hooks.forEach((h) => {
      const li = document.createElement("li");
      li.className = "list-group-item d-flex justify-content-between align-items-center";
      li.innerHTML = `${esc(h.url)} <span class="badge bg-success">${esc(h.event)}</span>
        <a href="#" class="del-wh text-danger" data-id="${h.id}">Удалить</a>`;
      ul.appendChild(li);
    });
    document.querySelectorAll(".del-wh").forEach((b) => b.addEventListener("click", async (e) => {
      e.preventDefault(); await api(`/api/webhooks/${b.dataset.id}`, "DELETE"); loadWebhooks();
    }));
  } catch (e) {}
}

async function loadSubscription() {
  try {
    const s = await api("/api/payments/status");
    document.getElementById("sub-status").innerHTML = s.active
      ? `<span class="text-success">Подписка активна: ${esc(s.plan)} до ${new Date(s.expires_at).toLocaleDateString()}</span>`
      : `<span class="text-muted">Нет активной подписки. Тариф: ${esc(s.plan)}</span>`;
  } catch (e) {}
}

document.querySelectorAll("[data-plan]").forEach((b) => b.addEventListener("click", async () => {
  try {
    const data = await api("/api/payments/create", "POST", { plan: b.dataset.plan });
    showAlert(`Оплата создана. Перейдите по ссылке: ${data.confirmation_url}`, "success");
    if (data.test_mode) window.location.href = data.confirmation_url;
  } catch (e) { showAlert(e.message); }
}));

// ---------- Suites ----------
let suitesCache = [];

async function loadSuites() {
  try {
    suitesCache = await api("/api/suites");
    const tbody = document.querySelector("#suites-table tbody");
    tbody.innerHTML = "";
    suitesCache.forEach((s) => {
      const stBadge = { passed: "success", failed: "danger", timeout: "warning", error: "danger" }[s.last_run_status] || "secondary";
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${esc(s.name)}</td><td><small>${esc(s.url)}</small></td>
        <td>${s.enabled_cases_count}/${s.cases_count}</td>
        <td>${s.last_run_id
          ? `<span class="badge bg-${stBadge}">${esc(s.last_run_status)}</span> <small class="text-muted">${new Date(s.last_run_at).toLocaleString()}</small>`
          : '<small class="text-muted">ещё не запускался</small>'}</td>
        <td>
          <a href="#" class="btn btn-sm btn-outline-success run-suite" data-id="${s.id}">Запустить</a>
          <a href="#" class="btn btn-sm btn-outline-primary edit-suite" data-id="${s.id}">Кейсы</a>
          ${s.last_run_id ? `<a href="#" class="btn btn-sm btn-outline-secondary view-run2" data-id="${s.last_run_id}">Отчёт</a>` : ""}
          <a href="#" class="btn btn-sm btn-outline-danger del-suite" data-id="${s.id}">Удалить</a>
        </td>`;
      tbody.appendChild(tr);
    });
    tbody.querySelectorAll(".run-suite").forEach((b) => b.addEventListener("click", async (e) => {
      e.preventDefault();
      const suite = suitesCache.find((x) => x.id === b.dataset.id);
      if (!confirm(`Запустить набор «${suite.name}» (${suite.enabled_cases_count} кейсов)?`)) return;
      try {
        const run = await api("/api/runs", "POST", {
          suite_id: b.dataset.id, url: suite.url, mode: suite.mode, name: suite.name, consent: true,
        });
        showAlert("Набор запущен", "success");
        startWsTimer(run.id);
        loadRuns();
      } catch (err) { showAlert(err.message); }
    }));
    tbody.querySelectorAll(".edit-suite").forEach((b) => b.addEventListener("click", (e) => {
      e.preventDefault();
      openSuiteEditor(b.dataset.id);
    }));
    tbody.querySelectorAll(".view-run2").forEach((b) => b.addEventListener("click", (e) => {
      e.preventDefault();
      openRunModal(b.dataset.id);
    }));
    tbody.querySelectorAll(".del-suite").forEach((b) => b.addEventListener("click", async (e) => {
      e.preventDefault();
      if (!confirm("Удалить набор и все его тест-кейсы?")) return;
      try { await api(`/api/suites/${b.dataset.id}`, "DELETE"); loadSuites(); } catch (err) { showAlert(err.message); }
    }));
  } catch (e) { showAlert(e.message); }
}

document.getElementById("run-cred-toggle").addEventListener("click", () => {
  document.getElementById("run-cred-form").classList.toggle("d-none");
});

document.getElementById("run-cr-save").addEventListener("click", async () => {
  try {
    const label = document.getElementById("run-cr-label").value || "Доступ";
    const resp = await api("/api/credentials", "POST", {
      label,
      login_url: document.getElementById("run-cr-url").value,
      username: document.getElementById("run-cr-user").value,
      password: document.getElementById("run-cr-pass").value,
    });
    document.getElementById("run-cr-pass").value = "";
    showAlert(`Доступ «${label}» сохранён и выбран для прогона`, "success");
    await loadCredentials();
    document.getElementById("run-cred").value = resp.id;
  } catch (e) { showAlert(e.message); }
});

document.getElementById("su-create").addEventListener("click", async () => {
  try {
    await api("/api/suites", "POST", {
      name: document.getElementById("su-name").value || "Новый набор",
      url: document.getElementById("su-url").value,
      mode: "specific",
      credential_id: document.getElementById("su-cred").value || null,
    });
    showAlert("Набор создан. Добавьте кейсы или сохраните их из прогона «Проверить текущую страницу».", "success");
    loadSuites();
  } catch (e) { showAlert(e.message); }
});

async function openSuiteEditor(suiteId) {
  try {
    const [suite, cases] = await Promise.all([
      api(`/api/suites/${suiteId}`),
      api(`/api/suites/${suiteId}/cases`),
    ]);
    const rows = cases.map((c) => `
      <tr data-id="${c.id}">
        <td><input class="form-check-input case-enabled" type="checkbox" ${c.enabled ? "checked" : ""}></td>
        <td>${c.order + 1}</td>
        <td><input class="form-control form-control-sm case-expected" value="${esc(c.expected)}" placeholder="Ожидаемый результат"></td>
        <td><select class="form-select form-select-sm case-action">
          ${["click", "fill", "select", "check_form", "fill_form", "validate_field", "assert_text", "goto"].map((a) =>
            `<option ${c.action === a ? "selected" : ""}>${a}</option>`).join("")}
        </select></td>
        <td><input class="form-control form-control-sm case-selector" value="${esc(c.selector)}"></td>
        <td><input class="form-control form-control-sm case-value" value="${esc(c.value)}"></td>
        <td>${c.source === "manual" ? '<span class="badge bg-info text-dark">ручной</span>' : '<span class="badge bg-light text-dark border">авто</span>'}</td>
        <td><a href="#" class="text-danger del-case">✕</a></td>
      </tr>`).join("");
    const html = `
      <div class="modal fade" tabindex="-1"><div class="modal-dialog modal-xl"><div class="modal-content">
        <div class="modal-header"><h5 class="modal-title">Набор «${esc(suite.name)}» — тест-кейсы</h5>
        <button class="btn-close" data-bs-dismiss="modal"></button></div>
        <div class="modal-body">
          <div class="row g-2 mb-3">
            <div class="col-md-6"><label class="form-label small">URL набора</label>
              <input class="form-control form-control-sm" id="se-url" value="${esc(suite.url)}"></div>
            <div class="col-md-6"><label class="form-label small">Доступ (авторизация на сайте)</label>
              <select class="form-select form-select-sm" id="se-cred"><option value="">— без авторизации —</option></select>
              <div class="small text-muted">Добавить новый доступ можно при запуске теста или в разделе «Настройки».</div>
            </div>
          </div>
          <table class="table table-sm">
            <thead><tr><th>Вкл</th><th>#</th><th>Тест-кейс (ожидаемый результат)</th><th>Действие</th><th>Селектор</th><th>Значение</th><th>Источник</th><th></th></tr></thead>
            <tbody id="cases-tbody">${rows}</tbody>
          </table>
          <div class="row g-2 align-items-end mb-3 border-top pt-3">
            <div class="col-md-4"><input class="form-control form-control-sm new-case-expected" placeholder="Новый кейс: ожидаемый результат"></div>
            <div class="col-md-2"><select class="form-select form-select-sm new-case-action">
              ${["click", "fill", "select", "check_form", "fill_form", "validate_field", "assert_text", "goto"].map((a) => `<option>${a}</option>`).join("")}
            </select></div>
            <div class="col-md-3"><input class="form-control form-control-sm new-case-selector" placeholder="селектор"></div>
            <div class="col-md-2"><input class="form-control form-control-sm new-case-value" placeholder="значение"></div>
            <div class="col-md-1"><button class="btn btn-sm btn-outline-primary w-100" id="add-case-btn">＋</button></div>
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-primary" id="save-cases-btn">Сохранить изменения</button>
        </div>
      </div></div></div>`;
    document.body.insertAdjacentHTML("beforeend", html);
    const modal = document.body.lastElementChild;
    new bootstrap.Modal(modal).show();

    // credentials dropdown (existing credentials only; creation happens on the run form / settings)
    const credSel = modal.querySelector("#se-cred");
    try {
      const creds = await api("/api/credentials");
      creds.forEach((c) => {
        credSel.innerHTML += `<option value="${c.id}" ${c.id === suite.credential_id ? "selected" : ""}>${esc(c.label)} (${esc(c.username)})</option>`;
      });
    } catch (e) {}

    modal.querySelector("#add-case-btn").addEventListener("click", async () => {
      try {
        await api(`/api/suites/${suiteId}/cases`, "POST", {
          action: modal.querySelector(".new-case-action").value,
          selector: modal.querySelector(".new-case-selector").value,
          value: modal.querySelector(".new-case-value").value,
          expected: modal.querySelector(".new-case-expected").value,
        });
        modal.querySelector(".btn-close").click();
        openSuiteEditor(suiteId);
      } catch (e) { showAlert(e.message); }
    });
    modal.querySelector("#cases-tbody").addEventListener("click", async (e) => {
      const del = e.target.closest(".del-case");
      if (!del) return;
      const tr = del.closest("tr[data-id]");
      try {
        await api(`/api/suites/cases/${tr.dataset.id}`, "DELETE");
        tr.remove();
      } catch (err) { showAlert(err.message); }
    });
    modal.querySelector("#save-cases-btn").addEventListener("click", async () => {
      try {
        await api(`/api/suites/${suiteId}`, "PATCH", {
          url: modal.querySelector("#se-url").value,
          credential_id: credSel.value,
        });
        for (const tr of modal.querySelectorAll("#cases-tbody tr[data-id]")) {
          await api(`/api/suites/cases/${tr.dataset.id}`, "PATCH", {
            enabled: tr.querySelector(".case-enabled").checked,
            expected: tr.querySelector(".case-expected").value,
            action: tr.querySelector(".case-action").value,
            selector: tr.querySelector(".case-selector").value,
            value: tr.querySelector(".case-value").value,
          });
        }
        modal.querySelector(".btn-close").click();
        showAlert("Изменения сохранены", "success");
        loadSuites();
      } catch (e) { showAlert(e.message); }
    });
    modal.addEventListener("hidden.bs.modal", () => modal.remove());
  } catch (e) { showAlert(e.message); }
}

// ---------- Credentials ----------
async function loadCredentials() {
  try {
    const creds = await api("/api/credentials");
    const ul = document.getElementById("cred-list");
    ul.innerHTML = "";
    creds.forEach((c) => {
      const li = document.createElement("li");
      li.className = "list-group-item d-flex justify-content-between align-items-center";
      li.innerHTML = `<span><strong>${esc(c.label)}</strong> — ${esc(c.login_url || "URL входа не указан")}
        <small class="text-muted ms-2">логин: ${esc(c.username)} · пароль скрыт · используется в наборах: ${c.used_by_suites}</small></span>
        <a href="#" class="text-danger del-cred" data-id="${c.id}">Удалить</a>`;
      ul.appendChild(li);
    });
    ul.querySelectorAll(".del-cred").forEach((b) => b.addEventListener("click", async (e) => {
      e.preventDefault();
      if (!confirm("Удалить доступ?")) return;
      try { await api(`/api/credentials/${b.dataset.id}`, "DELETE"); loadCredentials(); } catch (err) { showAlert(err.message); }
    }));
    // also refresh the run-form dropdown
    const sel = document.getElementById("run-cred");
    sel.innerHTML = '<option value="">— без авторизации —</option>';
    creds.forEach((c) => { sel.innerHTML += `<option value="${c.id}">${esc(c.label)} (${esc(c.username)})</option>`; });
  } catch (e) {}
}

document.getElementById("cr-add").addEventListener("click", async () => {
  try {
    await api("/api/credentials", "POST", {
      label: document.getElementById("cr-label").value || "Доступ",
      login_url: document.getElementById("cr-login-url").value,
      username: document.getElementById("cr-username").value,
      password: document.getElementById("cr-password").value,
    });
    document.getElementById("cr-password").value = "";
    showAlert("Доступ сохранён (пароль зашифрован)", "success");
    loadCredentials();
  } catch (e) { showAlert(e.message); }
});

// ---------- Navigation ----------
document.querySelectorAll("[data-view]").forEach((link) => {
  link.addEventListener("click", (e) => {
    e.preventDefault();
    const view = link.dataset.view;
    document.querySelectorAll("[data-view]").forEach((l) => l.classList.remove("active"));
    link.classList.add("active");
    document.getElementById("view-runs").classList.toggle("d-none", view !== "runs");
    document.getElementById("view-suites").classList.toggle("d-none", view !== "suites");
    document.getElementById("view-scenarios").classList.toggle("d-none", view !== "scenarios");
    document.getElementById("view-schedule").classList.toggle("d-none", view !== "schedule");
    document.getElementById("view-settings").classList.toggle("d-none", view !== "settings");
    document.getElementById("view-faq").classList.toggle("d-none", view !== "faq");
    document.getElementById("new-test-card").classList.toggle("d-none", view !== "runs");
    if (view === "runs") { loadStats(); loadRuns(); }
    if (view === "suites") loadSuites();
    if (view === "scenarios") loadScenarios();
    if (view === "schedule") loadSchedule();
    if (view === "faq" && window.renderFaqInto) {
      renderFaqInto(document.getElementById("faq-root"), document.getElementById("faq-search"));
    }
  });
});

document.getElementById("refresh-runs").addEventListener("click", () => { loadStats(); loadRuns(); });

// ---------- Init ----------
(async () => {
  if (accessToken) {
    try {
      await api("/api/auth/me");
      setAuthUI(true);
      await loadApp();
      return;
    } catch (e) {
      localStorage.removeItem("qa_access"); localStorage.removeItem("qa_refresh");
      accessToken = ""; refreshToken = "";
      showAlert(`Сессия завершена (${e.message}). Войдите заново.`, "warning");
    }
  }
  // Handle token in URL (email verification / reset)
  const params = new URLSearchParams(location.search);
  if (params.get("token")) {
    const action = params.get("action") || "verify";
    if (action === "reset") {
      document.getElementById("reset-token").value = params.get("token");
      document.getElementById("login-form").classList.add("d-none");
      document.getElementById("reset-form").classList.remove("d-none");
    } else {
      try {
        const data = await api(`/api/auth/verify-email?token=${params.get("token")}`);
        showAlert(data.message, "success");
      } catch (e) { showAlert(e.message); }
    }
  }
  setAuthUI(false);
})();