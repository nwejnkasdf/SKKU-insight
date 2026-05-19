const apiBase = window.__ADMIN_CONFIG__?.apiBase || "http://localhost:8000";

const state = {
  accessToken: localStorage.getItem("admin_access") || "",
  refreshToken: localStorage.getItem("admin_refresh") || "",
  adminEmail: localStorage.getItem("admin_email") || "admin@skkuinsight.org",
  mustChangePassword: localStorage.getItem("admin_must_change") === "true",
  loading: false,
  error: "",
  users: [],
  health: null,
  view: window.location.hash === "#account" ? "account" : "operations"
};

const root = document.querySelector("#root");

function setTokens(pair, email = state.adminEmail) {
  state.accessToken = pair.access_token;
  state.refreshToken = pair.refresh_token;
  state.adminEmail = email;
  state.mustChangePassword = Boolean(pair.must_change_password);
  localStorage.setItem("admin_access", state.accessToken);
  localStorage.setItem("admin_refresh", state.refreshToken);
  localStorage.setItem("admin_email", state.adminEmail);
  localStorage.setItem("admin_must_change", String(state.mustChangePassword));
}

function clearTokens() {
  state.accessToken = "";
  state.refreshToken = "";
  state.adminEmail = "admin@skkuinsight.org";
  state.mustChangePassword = false;
  localStorage.removeItem("admin_access");
  localStorage.removeItem("admin_refresh");
  localStorage.removeItem("admin_email");
  localStorage.removeItem("admin_must_change");
}

async function request(path, options = {}, retry = true) {
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {})
  };
  if (state.accessToken) headers.Authorization = `Bearer ${state.accessToken}`;
  const response = await fetch(`${apiBase}${path}`, { ...options, headers });
  if (response.status === 401 && retry && state.refreshToken) {
    const refreshed = await fetch(`${apiBase}/admin/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: state.refreshToken })
    });
    if (refreshed.ok) {
      setTokens(await refreshed.json());
      return request(path, options, false);
    }
    clearTokens();
  }
  if (!response.ok) {
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    const error = new Error(payload?.message || payload?.detail || `HTTP ${response.status}`);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  if (response.status === 204) return null;
  return response.json();
}

async function login(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  state.loading = true;
  state.error = "";
  render();
  try {
    const email = String(data.get("email") || "").trim();
    const pair = await request("/admin/auth/login", {
      method: "POST",
      body: JSON.stringify({
        email,
        password: String(data.get("password") || "")
      })
    }, false);
    setTokens(pair, email);
    await loadDashboard();
  } catch (error) {
    state.error = messageForError(error);
  } finally {
    state.loading = false;
    render();
  }
}

async function changePassword(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  state.loading = true;
  state.error = "";
  render();
  try {
    await request("/admin/auth/change-password", {
      method: "POST",
      body: JSON.stringify({
        current_password: data.get("currentPassword"),
        new_password: data.get("newPassword")
      })
    });
    clearTokens();
    state.error = "비밀번호가 변경되었습니다. 새 비밀번호로 다시 로그인해주세요.";
  } catch (error) {
    state.error = messageForError(error);
  } finally {
    state.loading = false;
    render();
  }
}

async function logout() {
  try {
    await request("/admin/auth/logout", {
      method: "POST",
      body: JSON.stringify({ refresh_token: state.refreshToken })
    });
  } catch {
    // local logout still wins
  }
  clearTokens();
  render();
}

async function loadDashboard() {
  state.loading = true;
  state.error = "";
  render();
  try {
    const [users, health] = await Promise.all([
      request("/admin/users?limit=20"),
      loadHealth()
    ]);
    state.users = users.items || [];
    state.health = health;
  } catch (error) {
    if (error.status === 409) {
      state.mustChangePassword = true;
      localStorage.setItem("admin_must_change", "true");
    }
    state.error = messageForError(error);
  } finally {
    state.loading = false;
    render();
  }
}

async function loadHealth() {
  try {
    const response = await fetch(`${apiBase}/health`);
    return response.ok ? "정상" : "점검 필요";
  } catch {
    return "점검 필요";
  }
}

function messageForError(error) {
  if (error?.payload?.code === "admin.must_change_password") return "첫 로그인 후 비밀번호 변경이 필요합니다.";
  return error?.message || "요청을 처리하지 못했습니다.";
}

function formatDate(value) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("ko-KR", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}

function render() {
  if (!state.accessToken) {
    root.innerHTML = loginView();
    bindLogin();
    return;
  }
  if (state.mustChangePassword) {
    root.innerHTML = changePasswordView();
    bindPasswordChange();
    return;
  }
  root.innerHTML = shellView();
  bindApp();
}

function loginView() {
  return `
    <section class="loginWrap">
      <form class="loginCard form" id="loginForm">
        <div class="brand">
          <div class="brandMark">IN</div>
          <div>
            <strong>SKKU InSight</strong>
            <div class="muted">관리자 콘솔</div>
          </div>
        </div>
        <label>
          <span class="meta">관리자 이메일</span>
          <input name="email" type="email" value="admin@skkuinsight.org" autocomplete="username" required />
        </label>
        <label>
          <span class="meta">비밀번호</span>
          <input name="password" type="password" autocomplete="current-password" required />
        </label>
        ${state.error ? `<p class="${state.error.includes("변경") ? "notice" : "error"}">${state.error}</p>` : ""}
        <button type="submit" ${state.loading ? "disabled" : ""}>관리자 로그인</button>
      </form>
    </section>
  `;
}

function changePasswordView() {
  return `
    <section class="loginWrap">
      <form class="loginCard form" id="passwordForm">
        <div>
          <h1>초기 비밀번호 변경</h1>
          <p class="muted">부트스트랩 관리자 계정은 첫 로그인 후 새 비밀번호가 필요합니다.</p>
        </div>
        <label>
          <span class="meta">현재 비밀번호</span>
          <input name="currentPassword" type="password" autocomplete="current-password" required />
        </label>
        <label>
          <span class="meta">새 비밀번호</span>
          <input name="newPassword" type="password" autocomplete="new-password" required />
        </label>
        ${state.error ? `<p class="error">${state.error}</p>` : ""}
        <button type="submit" ${state.loading ? "disabled" : ""}>변경하기</button>
        <button class="secondary" type="button" id="logoutButton">로그아웃</button>
      </form>
    </section>
  `;
}

function shellView() {
  const isAccount = state.view === "account";
  return `
    <div class="shell">
      <aside class="sidebar">
        <div class="brand">
          <div class="brandMark">IN</div>
          <div>
            <strong>SKKU InSight</strong>
            <div class="muted">관리자 콘솔</div>
          </div>
        </div>
        <nav class="nav">
          ${navButton("operations", "운영")}
          ${navButton("account", "내 계정")}
        </nav>
        <button class="secondary" id="logoutButton">로그아웃</button>
      </aside>
      <section class="main">
        <header class="pageTitle">
          <div>
            <h1>${isAccount ? "내 계정" : "운영"}</h1>
            <p>${isAccount ? "관리자 세션과 계정 작업을 확인합니다." : "사용자 상태와 시스템 상태를 확인합니다."}</p>
          </div>
        </header>
        ${state.error ? `<p class="notice">${state.error}</p>` : ""}
        ${isAccount ? accountView() : operationsView()}
      </section>
    </div>
  `;
}

function navButton(view, label) {
  return `<button class="${state.view === view ? "active" : ""}" type="button" data-view="${view}">${label}</button>`;
}

function operationsView() {
  const activeUsers = state.users.filter((user) => user.consent_active).length;
  const inactiveUsers = state.users.length - activeUsers;
  const pendingDeletion = state.users.filter((user) => user.deletion_pending).length;
  const consentRate = state.users.length === 0 ? 0 : Math.round((activeUsers / state.users.length) * 100);
  return `
    <section class="grid">
      ${metricCard("사용자", state.users.length, "등록 계정")}
      ${metricCard("동의 활성", activeUsers, "추천 가능")}
      ${metricCard("동의율", `${consentRate}%`, "활성 비율")}
      ${metricCard("삭제 대기", pendingDeletion, "예약 계정")}
    </section>
    <section class="opsGrid">
      <div class="card">
        <div class="cardHead">
          <h2>상태 점검</h2>
          <button class="iconRefresh" type="button" title="시스템 상태 새로고침" data-refresh>↻</button>
        </div>
        <div class="statusList">
          ${statusRow("API", state.health || "확인 중", state.health === "정상")}
          ${statusRow("사용자 조회", `${state.users.length}건`, true)}
          ${statusRow("관리자 세션", state.accessToken ? "활성" : "없음", Boolean(state.accessToken))}
        </div>
      </div>
      <div class="card">
        <div class="cardHead">
          <h2>동의 분포</h2>
          <button class="iconRefresh" type="button" title="동의 분포 새로고침" data-refresh>↻</button>
        </div>
        <div class="barBlock">
          ${barRow("활성", activeUsers, state.users.length, "")}
          ${barRow("비활성", inactiveUsers, state.users.length, "warn")}
          ${barRow("삭제 대기", pendingDeletion, state.users.length, "danger")}
        </div>
      </div>
    </section>
    <section class="contentStack">
      <div class="card">
        <div class="cardHead">
          <h2>최근 사용자</h2>
          <button class="iconRefresh" type="button" title="최근 사용자 새로고침" data-refresh>↻</button>
        </div>
        ${usersTable(state.users.slice(0, 8))}
      </div>
    </section>
  `;
}

function accountView() {
  const session = readSession();
  return `
    <section class="accountGrid">
      <div class="card">
        <div class="cardHead">
          <h2>관리자 계정</h2>
          <span class="status">활성</span>
        </div>
        <div class="statusList">
          ${statusRow("이메일", state.adminEmail, true)}
          ${statusRow("권한", "관리자", true)}
          ${statusRow("토큰 만료", session.expiresAt, session.valid)}
        </div>
      </div>
      <div class="card">
        <h2>계정 작업</h2>
        <p class="muted">현재 세션을 종료하면 관리자 로그인 화면으로 돌아갑니다.</p>
        <button type="button" id="accountLogoutButton">로그아웃</button>
      </div>
    </section>
  `;
}

function metricCard(title, value, caption) {
  return `
    <div class="card">
      <span class="panelTitle">${title}</span>
      <strong class="metric">${value}</strong>
      <span class="muted">${caption}</span>
    </div>
  `;
}

function statusRow(label, value, ok) {
  return `
    <span>
      <b>${label}</b>
      <em class="${ok ? "" : "warnText"}">${escapeHtml(value)}</em>
    </span>
  `;
}

function barRow(label, value, total, tone) {
  const width = total === 0 ? 0 : Math.max(6, Math.round((value / total) * 100));
  return `
    <span class="barRow">
      <b>${label}</b>
      <i><em class="${tone}" style="width: ${width}%"></em></i>
      <strong>${value}</strong>
    </span>
  `;
}

function usersTable(users) {
  if (users.length === 0) return `<p class="muted">표시할 사용자가 없습니다.</p>`;
  return `
    <div class="table">
      <div class="row header">
        <span>이메일</span><span>동의</span><span>삭제 대기</span><span>가입일</span>
      </div>
      ${users.map((user) => `
        <div class="row">
          <strong>${escapeHtml(user.email)}</strong>
          <span class="status ${user.consent_active ? "" : "warn"}">${user.consent_active ? "활성" : "비활성"}</span>
          <span class="status ${user.deletion_pending ? "danger" : ""}">${user.deletion_pending ? "대기" : "없음"}</span>
          <span class="muted">${formatDate(user.created_at)}</span>
        </div>
      `).join("")}
    </div>
  `;
}

function bindLogin() {
  document.querySelector("#loginForm")?.addEventListener("submit", login);
}

function bindPasswordChange() {
  document.querySelector("#passwordForm")?.addEventListener("submit", changePassword);
  document.querySelector("#logoutButton")?.addEventListener("click", logout);
}

function bindApp() {
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => {
      state.view = button.getAttribute("data-view") || "operations";
      window.location.hash = state.view === "account" ? "account" : "operations";
      render();
    });
  });
  document.querySelectorAll("[data-refresh]").forEach((button) => {
    button.addEventListener("click", loadDashboard);
  });
  document.querySelector("#logoutButton")?.addEventListener("click", logout);
  document.querySelector("#accountLogoutButton")?.addEventListener("click", logout);
}

function readSession() {
  try {
    const [, payload] = state.accessToken.split(".");
    const normalized = payload.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
    const parsed = JSON.parse(atob(padded));
    const expiresAt = parsed.exp
      ? new Intl.DateTimeFormat("ko-KR", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(parsed.exp * 1000))
      : "-";
    return { expiresAt, valid: Boolean(parsed.exp && parsed.exp * 1000 > Date.now()) };
  } catch {
    return { expiresAt: "-", valid: false };
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

if (state.accessToken && !state.mustChangePassword) {
  loadDashboard();
} else {
  render();
}
