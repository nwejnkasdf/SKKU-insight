const apiBase = window.__ADMIN_CONFIG__?.apiBase || "http://localhost:8000";

const state = {
  accessToken: localStorage.getItem("admin_access") || "",
  refreshToken: localStorage.getItem("admin_refresh") || "",
  mustChangePassword: localStorage.getItem("admin_must_change") === "true",
  loading: false,
  error: "",
  users: [],
  probes: {}
};

const root = document.querySelector("#root");

function setTokens(pair) {
  state.accessToken = pair.access_token;
  state.refreshToken = pair.refresh_token;
  state.mustChangePassword = Boolean(pair.must_change_password);
  localStorage.setItem("admin_access", state.accessToken);
  localStorage.setItem("admin_refresh", state.refreshToken);
  localStorage.setItem("admin_must_change", String(state.mustChangePassword));
}

function clearTokens() {
  state.accessToken = "";
  state.refreshToken = "";
  state.mustChangePassword = false;
  localStorage.removeItem("admin_access");
  localStorage.removeItem("admin_refresh");
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
    const pair = await request("/admin/auth/login", {
      method: "POST",
      body: JSON.stringify({
        email: String(data.get("email") || "").trim(),
        password: String(data.get("password") || "")
      })
    }, false);
    setTokens(pair);
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
    const users = await request("/admin/users?limit=20");
    state.users = users.items || [];
    await Promise.all([
      probe("collectionJobs", "/admin/collection/jobs?limit=5"),
      probe("collectionStats", "/admin/collection/stats"),
      probe("clickbaitStats", "/admin/clickbait/stats"),
      probe("topicErrors", "/admin/topic-linkage/errors?limit=5"),
      probe("reprocess", "/admin/reprocess-requests?limit=5")
    ]);
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

async function probe(key, path) {
  try {
    const data = await request(path);
    state.probes[key] = { ok: true, data };
  } catch (error) {
    state.probes[key] = {
      ok: false,
      status: error.status || 0,
      message: error.status >= 500 ? "백엔드 미구현" : messageForError(error)
    };
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
        <p class="muted">API: ${apiBase}</p>
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
  const failedProbeCount = Object.values(state.probes).filter((item) => item && !item.ok).length;
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
          <a href="#overview" class="active">운영 요약</a>
          <a href="#users">사용자</a>
          <a href="#jobs">작업 상태</a>
        </nav>
        <button class="secondary" id="logoutButton">로그아웃</button>
      </aside>
      <section class="main">
        <header class="pageTitle">
          <div>
            <h1>관리자 콘솔</h1>
            <p>사용자 상태와 백엔드 운영 API 준비 상태를 확인합니다.</p>
          </div>
          <button id="refreshButton" ${state.loading ? "disabled" : ""}>새로고침</button>
        </header>
        ${state.error ? `<p class="notice">${state.error}</p>` : ""}
        <section class="grid" id="overview">
          ${metricCard("사용자", state.users.length, "관리자 API 연결됨")}
          ${metricCard("미구현 API", failedProbeCount, "stub 영역")}
          ${metricCard("API Base", apiBase.replace(/^https?:\/\//, ""), "현재 연결")}
        </section>
        <section class="wideGrid">
          <div class="card" id="users">
            <h2>사용자 목록</h2>
            ${usersTable()}
          </div>
          <div class="stack" id="jobs">
            ${probeCard("수집 작업", "collectionJobs")}
            ${probeCard("수집 통계", "collectionStats")}
            ${probeCard("낚시성 통계", "clickbaitStats")}
            ${probeCard("토픽 연결 오류", "topicErrors")}
            ${probeCard("재실행 이력", "reprocess")}
          </div>
        </section>
      </section>
    </div>
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

function usersTable() {
  if (state.users.length === 0) return `<p class="muted">표시할 사용자가 없습니다.</p>`;
  return `
    <div class="table">
      <div class="row header">
        <span>이메일</span><span>동의</span><span>삭제 대기</span><span>가입일</span>
      </div>
      ${state.users.map((user) => `
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

function probeCard(title, key) {
  const probeResult = state.probes[key];
  if (!probeResult) {
    return `
      <div class="card">
        <h2>${title}</h2>
        <span class="status warn">확인 전</span>
      </div>
    `;
  }
  if (probeResult.ok) {
    const count = Array.isArray(probeResult.data?.items) ? probeResult.data.items.length : "-";
    return `
      <div class="card">
        <h2>${title}</h2>
        <span class="status">연결됨</span>
        <p class="muted">표시 항목 ${count}</p>
      </div>
    `;
  }
  return `
    <div class="card">
      <h2>${title}</h2>
      <span class="status warn">${probeResult.message}</span>
      <p class="muted">HTTP ${probeResult.status || "-"}</p>
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
  document.querySelector("#refreshButton")?.addEventListener("click", loadDashboard);
  document.querySelector("#logoutButton")?.addEventListener("click", logout);
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
