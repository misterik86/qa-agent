const API = window.location.origin;
let accessToken = localStorage.getItem("qa_access") || "";

function headers(extra = {}) {
  return { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json", ...extra };
}

async function api(path, method = "GET", body = null) {
  const opts = { method, headers: headers() };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(API + path, opts);
  if (resp.status === 401) {
    localStorage.removeItem("qa_access");
    localStorage.removeItem("qa_refresh");
    window.location.href = "/dashboard.html";
    return null;
  }
  if (resp.status === 403) {
    showGate();
    return null;
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
  return data;
}

function showAlert(msg, type = "danger") {
  const area = document.getElementById("alert-area");
  area.innerHTML = `<div class="alert alert-${type} alert-dismissible fade show" role="alert">
    ${msg}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>`;
  setTimeout(() => { area.innerHTML = ""; }, 6000);
}

function showGate() {
  document.getElementById("content").classList.add("d-none");
  document.getElementById("gate").classList.remove("d-none");
}

const fmtDate = (iso) => iso ? new Date(iso).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" }) : "—";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const PLAN_BADGE = {
  trial: "bg-info text-dark", pro: "bg-success", enterprise: "bg-primary",
  enterprise_trial: "bg-warning text-dark", blocked: "bg-danger",
};
const STATUS_BADGE = {
  passed: "bg-success", failed: "bg-danger", running: "bg-primary",
  queued: "bg-secondary", cancelled: "bg-warning text-dark", pending: "bg-secondary", succeeded: "bg-success",
};

/* ---------- Stats ---------- */
async function loadStats() {
  const s = await api("/api/admin/stats");
  if (!s) return;
  const cards = [
    ["Пользователей", s.users_total, `${s.users_verified} подтверждено · ${s.users_blocked} заблокировано · ${s.users_admins} админов`],
    ["Новых за сутки", s.users_new_today, ""],
    ["Прогонов всего", s.runs_total, `passed: ${s.runs_passed} · failed: ${s.runs_failed} · активных: ${s.runs_active}`],
    ["Прогонов за сутки", s.runs_today, ""],
    ["Платежей", s.payments_total, `успешных на сумму ${(s.payments_sum / 100).toFixed(2)} ₽`],
  ];
  document.getElementById("stats-cards").innerHTML = cards.map(([title, val, sub]) => `
    <div class="col-md-4 col-lg">
      <div class="card h-100"><div class="card-body">
        <div class="text-muted small">${title}</div>
        <div class="fs-3 fw-bold">${val}</div>
        <div class="small text-muted">${esc(sub)}</div>
      </div></div>
    </div>`).join("");
}

/* ---------- Users ---------- */
async function loadUsers(search = "") {
  const q = search ? `&search=${encodeURIComponent(search)}` : "";
  const users = await api(`/api/admin/users?limit=200${q}`);
  if (!users) return;
  const tbody = document.getElementById("users-tbody");
  tbody.innerHTML = users.map((u) => `
    <tr data-id="${u.id}">
      <td>${u.is_admin ? '<span class="badge bg-dark me-1">admin</span>' : ""}${esc(u.email)}</td>
      <td>${esc(u.full_name)}</td>
      <td><span class="badge ${PLAN_BADGE[u.plan] || "bg-secondary"}">${u.plan}</span></td>
      <td>${u.runs_count}</td>
      <td>${u.is_verified ? '<span class="text-success">подтв.</span>' : '<span class="text-muted">не подтв.</span>'}
          ${u.plan === "blocked" ? '<span class="text-danger ms-1">блок</span>' : ""}</td>
      <td class="small text-muted">${fmtDate(u.created_at)}</td>
      <td class="text-end">
        <select class="form-select form-select-sm d-inline-block w-auto plan-select" title="Сменить тариф">
          ${["trial", "pro", "enterprise", "enterprise_trial"].map((p) =>
            `<option value="${p}" ${u.plan === p ? "selected" : ""}>${p}</option>`).join("")}
        </select>
        ${u.plan === "blocked"
          ? `<button class="btn btn-sm btn-outline-success act-unblock">Разблокировать</button>`
          : `<button class="btn btn-sm btn-outline-danger act-block">Блокировать</button>`}
        <button class="btn btn-sm btn-outline-secondary act-toggle-admin" data-next="${u.is_admin ? "0" : "1"}">
          ${u.is_admin ? "Снять админа" : "Сделать админом"}
        </button>
      </td>
    </tr>`).join("");
}

document.getElementById("users-tbody").addEventListener("click", async (e) => {
  const tr = e.target.closest("tr[data-id]");
  if (!tr) return;
  const id = tr.dataset.id;
  try {
    let resp;
    if (e.target.classList.contains("act-block")) {
      resp = await api(`/api/admin/users/${id}`, "PATCH", { plan: "blocked" });
    } else if (e.target.classList.contains("act-unblock")) {
      resp = await api(`/api/admin/users/${id}`, "PATCH", { plan: "trial" });
    } else if (e.target.classList.contains("act-toggle-admin")) {
      resp = await api(`/api/admin/users/${id}`, "PATCH", { is_admin: e.target.dataset.next === "1" });
    }
    if (resp) { await loadUsers(document.getElementById("user-search").value); showAlert("Изменения сохранены", "success"); }
  } catch (err) { showAlert(err.message); }
});

document.getElementById("users-tbody").addEventListener("change", async (e) => {
  if (!e.target.classList.contains("plan-select")) return;
  const tr = e.target.closest("tr[data-id]");
  try {
    const resp = await api(`/api/admin/users/${tr.dataset.id}`, "PATCH", { plan: e.target.value });
    if (resp) { await loadUsers(document.getElementById("user-search").value); showAlert("Тариф обновлён", "success"); }
  } catch (err) { showAlert(err.message); }
});

document.getElementById("user-search-btn").addEventListener("click", () => loadUsers(document.getElementById("user-search").value));
document.getElementById("user-search").addEventListener("keydown", (e) => {
  if (e.key === "Enter") loadUsers(e.target.value);
});

/* ---------- Runs / Payments / Audit ---------- */
async function loadRuns(status = "") {
  const q = status ? `&status=${status}` : "";
  const runs = await api(`/api/admin/runs?limit=100${q}`);
  if (!runs) return;
  document.getElementById("runs-tbody").innerHTML = runs.map((r) => `
    <tr>
      <td class="small text-muted">${fmtDate(r.created_at)}</td>
      <td>${esc(r.owner_email)}</td>
      <td class="text-truncate" style="max-width:260px" title="${esc(r.url)}">${esc(r.url)}</td>
      <td>${r.mode}</td>
      <td><span class="badge ${STATUS_BADGE[r.status] || "bg-secondary"}">${r.status}</span></td>
      <td class="small">${r.passed_steps}/${r.total_steps}${r.failed_steps ? ` <span class="text-danger">(fail: ${r.failed_steps})</span>` : ""}</td>
      <td><a href="/dashboard.html" class="small" onclick="sessionStorage.setItem('qa_open_run','${r.id}')">открыть</a></td>
    </tr>`).join("");
}

async function loadPayments() {
  const rows = await api("/api/admin/payments?limit=200");
  if (!rows) return;
  document.getElementById("payments-tbody").innerHTML = rows.map((p) => `
    <tr>
      <td class="small text-muted">${fmtDate(p.created_at)}</td>
      <td>${esc(p.user_email)}</td>
      <td>${p.plan}</td>
      <td>${(p.amount / 100).toFixed(2)} ${p.currency}</td>
      <td><span class="badge ${STATUS_BADGE[p.status] || "bg-secondary"}">${p.status}</span></td>
    </tr>`).join("");
}

async function loadAudit() {
  const rows = await api("/api/admin/audit?limit=300");
  if (!rows) return;
  document.getElementById("audit-tbody").innerHTML = rows.map((a) => `
    <tr>
      <td class="small text-muted">${fmtDate(a.created_at)}</td>
      <td>${esc(a.user_email ?? "—")}</td>
      <td><span class="badge bg-light text-dark border">${a.event}</span></td>
      <td class="small">${esc(a.entity_type)}${a.entity_id ? `:${esc(a.entity_id).slice(0, 8)}` : ""}</td>
      <td class="small text-muted">${esc(a.ip)}</td>
      <td class="small text-muted text-truncate" style="max-width:280px">${esc(a.meta)}</td>
    </tr>`).join("");
}

/* ---------- Navigation ---------- */
const loaders = { stats: loadStats, users: () => loadUsers(), runs: () => loadRuns(), payments: loadPayments, audit: loadAudit };

document.querySelectorAll("#anav .nav-link").forEach((link) => {
  link.addEventListener("click", (e) => {
    e.preventDefault();
    document.querySelectorAll("#anav .nav-link").forEach((l) => l.classList.remove("active"));
    link.classList.add("active");
    const view = link.dataset.view;
    document.querySelectorAll("[data-section]").forEach((s) => s.classList.toggle("d-none", s.dataset.section !== view));
    loaders[view] && loaders[view]();
  });
});

document.getElementById("run-status-filter").addEventListener("change", (e) => loadRuns(e.target.value));
document.getElementById("logout-btn").addEventListener("click", () => {
  localStorage.removeItem("qa_access");
  localStorage.removeItem("qa_refresh");
  window.location.href = "/dashboard.html";
});

/* ---------- Init ---------- */
(async function init() {
  if (!accessToken) { showGate(); return; }
  try {
    const me = await api("/api/auth/me");
    if (!me) return;
    if (!me.is_admin) { showGate(); return; }
    document.getElementById("admin-email").textContent = me.email;
    document.getElementById("content").classList.remove("d-none");
    loadStats();
  } catch (err) {
    showAlert(err.message);
    showGate();
  }
})();
