const apiBase = window.__ADMIN_CONFIG__?.apiBase || "http://localhost:8000";

const state = {
  accessToken: localStorage.getItem("admin_access") || "",
  refreshToken: localStorage.getItem("admin_refresh") || "",
  adminEmail: "",
  mustChangePassword: localStorage.getItem("admin_must_change") === "true",
  loading: false,
  error: "",
  notice: "",
  noticeTone: "success",
  authMode: "login",
  collectionBusyUserId: "",
  collectionRowMessages: {},
  collectionPollTimer: null,
  collectionTickTimer: null,
  collectionAutoRefreshTimer: null,
  users: [],
  health: null,
  view: window.location.hash === "#account" ? "account" : "operations"
};

const root = document.querySelector("#root");
const brandIcon = `
  <svg aria-hidden="true" viewBox="0 0 24 24">
    <path d="M6 3v11" />
    <path d="M18 5a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z" />
    <path d="M6 14a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z" />
    <path d="M6 14c0-4 3-6 9-6" />
  </svg>
`;
const navIcons = {
  operations: `
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <path d="M4 19V5" />
      <path d="M8 19v-7" />
      <path d="M12 19v-4" />
      <path d="M16 19V9" />
      <path d="M20 19V7" />
    </svg>
  `,
  account: `
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <path d="M12 3 5 6v5c0 4.5 2.8 8.4 7 10 4.2-1.6 7-5.5 7-10V6l-7-3Z" />
      <path d="M9 12l2 2 4-5" />
    </svg>
  `
};

function brandBlock(subtitle = "관리자 콘솔") {
  return `
    <div class="brand">
      <div class="brandMark">${brandIcon}</div>
      <div>
        <strong>SKKU InSight</strong>
        <span>${subtitle}</span>
      </div>
    </div>
  `;
}

function setTokens(pair, email = state.adminEmail) {
  state.accessToken = pair.access_token;
  state.refreshToken = pair.refresh_token;
  state.adminEmail = email;
  state.mustChangePassword = Boolean(pair.must_change_password);
  localStorage.setItem("admin_access", state.accessToken);
  localStorage.setItem("admin_refresh", state.refreshToken);
  localStorage.removeItem("admin_email");
  localStorage.setItem("admin_must_change", String(state.mustChangePassword));
}

function clearTokens() {
  state.accessToken = "";
  state.refreshToken = "";
  state.adminEmail = "";
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
    const detail = payload?.detail;
    const message = payload?.message || (typeof detail === "string" ? detail : detail?.message) || `HTTP ${response.status}`;
    const error = new Error(message);
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
    updateCollectionPolling();
  }
}

async function signup(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  state.loading = true;
  state.error = "";
  render();
  try {
    const email = String(data.get("email") || "").trim();
    const pair = await request("/admin/auth/signup", {
      method: "POST",
      body: JSON.stringify({
        email,
        password: String(data.get("password") || ""),
        signup_code: String(data.get("signupCode") || "").trim()
      })
    }, false);
    setTokens(pair, email);
    await loadDashboard();
  } catch (error) {
    state.error = messageForError(error);
  } finally {
    state.loading = false;
    render();
    updateCollectionPolling();
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
  stopCollectionPolling();
  stopCollectionAutoRefresh();
}

async function loadDashboard() {
  state.loading = true;
  state.error = "";
  state.notice = "";
  state.noticeTone = "success";
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
    updateCollectionPolling();
    updateCollectionAutoRefresh();
  }
}

async function runCollectionForUser(userId, email) {
  if (!userId) return;
  state.collectionBusyUserId = userId;
  state.error = "";
  state.notice = "";
  setMessage("", "success");
  setUserCollectionSnapshot(userId, {
    latest_collection_status: "queued",
    latest_collection_created_at: new Date().toISOString(),
    latest_collection_started_at: null,
    latest_collection_finished_at: null
  });
  try {
    const result = await request(`/admin/users/${userId}/collection/run-now`, {
      method: "POST"
    });
    await refreshUsersTable();
    startCollectionPolling();
  } catch (error) {
    setCollectionRowMessage(userId, messageForError(error), error.status === 409 ? "loading" : "warn");
    try {
      await refreshUsersTable();
    } catch {
      // keep the inline message; the next manual refresh will reconcile the row.
    }
  } finally {
    state.collectionBusyUserId = "";
    renderUsersTableOnly();
    updateCollectionPolling();
  }
}

async function triggerUserProfileCron() {
  // C-62 후속 (2026-05-26) — Discovery slot 의존 UserProfile cron 수동 트리거.
  state.notice = "";
  state.error = "";
  try {
    await request("/admin/cron/user-profile/trigger", { method: "POST" });
    setMessage("UserProfile cron 큐잉됨 — ~30s 후 사용자 dashboard refresh 시 discovery 적용.", "success");
  } catch (error) {
    setMessage(messageForError(error), "warn");
  }
  render();
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

function messageView() {
  if (state.error) return `<p class="notice">${escapeHtml(state.error)}</p>`;
  if (state.notice) return `<p class="notice ${state.noticeTone === "success" ? "success" : ""}">${escapeHtml(state.notice)}</p>`;
  return "";
}

function setMessage(message, tone = "success") {
  state.error = tone === "warn" ? message : "";
  state.notice = tone === "warn" ? "" : message;
  state.noticeTone = tone;
  const area = document.querySelector("#messageArea");
  if (area) area.innerHTML = messageView();
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

function latestCollectionTime(user) {
  return user.latest_collection_finished_at
    || user.latest_collection_started_at
    || user.latest_collection_created_at
    || "";
}

function collectionStartTime(user) {
  return user.latest_collection_started_at || user.latest_collection_created_at || "";
}

function collectionElapsedMs(user) {
  const start = collectionStartTime(user);
  if (!start) return null;
  const end = isCollectionInFlight(user)
    ? Date.now()
    : (user.latest_collection_finished_at ? new Date(user.latest_collection_finished_at).getTime() : Date.now());
  const startMs = new Date(start).getTime();
  if (!Number.isFinite(startMs) || !Number.isFinite(end)) return null;
  return Math.max(0, end - startMs);
}

function formatDuration(ms) {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes === 0) return `${seconds}초`;
  return `${minutes}분 ${String(seconds).padStart(2, "0")}초`;
}

function collectionTimeText(user) {
  const elapsed = collectionElapsedMs(user);
  if (isCollectionInFlight(user)) {
    return elapsed === null ? "경과 계산 중" : `경과 ${formatDuration(elapsed)}`;
  }
  if (user.latest_collection_status === "succeeded" && elapsed !== null) {
    return `소요 ${formatDuration(elapsed)}`;
  }
  const time = latestCollectionTime(user);
  return time ? formatDate(time) : "-";
}

function collectionStampText(user) {
  if (isCollectionInFlight(user)) {
    const start = collectionStartTime(user);
    return start ? formatDate(start) : "";
  }
  if (user.latest_collection_finished_at) {
    return formatDate(user.latest_collection_finished_at);
  }
  return "";
}

function collectionStatusLabel(status) {
  return {
    queued: "대기",
    running: "실행 중",
    succeeded: "완료",
    failed: "실패",
    skipped: "건너뜀"
  }[status] || "없음";
}

function collectionStatusTone(status) {
  if (status === "failed") return "danger";
  if (status === "queued" || status === "skipped") return "warn";
  return "";
}

function isCollectionInFlight(user) {
  return user.latest_collection_status === "queued" || user.latest_collection_status === "running";
}

function collectionMeta(user) {
  const status = user.latest_collection_status || "";
  const inline = state.collectionRowMessages[user.user_id];
  const loading = state.collectionBusyUserId === user.user_id || inline?.tone === "loading" || isCollectionInFlight(user);
  return `
    <span class="collectionMeta" data-collection-meta="${escapeHtml(user.user_id)}">
      <span class="collectionLine">
        ${loading ? `<i class="spinner" aria-hidden="true"></i>` : ""}
        <b class="status ${collectionStatusTone(status)}">${collectionStatusLabel(status)}</b>
        <em data-collection-time="${escapeHtml(user.user_id)}">${collectionTimeText(user)}</em>
      </span>
      ${collectionStampText(user) ? `<small class="collectionStamp">${collectionStampText(user)}</small>` : ""}
      ${inline ? `<small class="collectionInline ${inline.tone}">${escapeHtml(inline.text)}</small>` : ""}
    </span>
  `;
}

function setUserCollectionSnapshot(userId, snapshot) {
  const user = state.users.find((item) => item.user_id === userId);
  if (!user) return;
  Object.assign(user, snapshot);
  renderUsersTableOnly();
}

async function refreshUsersTable() {
  const users = await request("/admin/users?limit=20");
  state.users = users.items || [];
  reconcileCollectionRowMessages();
  renderUsersTableOnly();
  updateCollectionPolling();
  updateCollectionAutoRefresh();
}

function renderUsersTableOnly() {
  const mount = document.querySelector("#usersTableMount");
  if (!mount) return;
  mount.innerHTML = usersTable(state.users.slice(0, 8));
  bindCollectionButtons();
}

function setCollectionRowMessage(userId, text, tone = "loading") {
  if (text) {
    state.collectionRowMessages[userId] = { text, tone };
  } else {
    delete state.collectionRowMessages[userId];
  }
  renderUsersTableOnly();
}

function reconcileCollectionRowMessages() {
  state.users.forEach((user) => {
    const inline = state.collectionRowMessages[user.user_id];
    if (!inline) return;
    if (user.latest_collection_status === "succeeded") {
      delete state.collectionRowMessages[user.user_id];
    } else if (user.latest_collection_status === "failed") {
      state.collectionRowMessages[user.user_id] = { text: "수집 실패", tone: "warn" };
    } else if (isCollectionInFlight(user)) {
      delete state.collectionRowMessages[user.user_id];
    }
  });
}

function hasCollectionPollingWork() {
  if (state.view !== "operations") return false;
  return state.users.slice(0, 8).some((user) => {
    const inline = state.collectionRowMessages[user.user_id];
    return state.collectionBusyUserId === user.user_id
      || isCollectionInFlight(user)
      || inline?.tone === "loading";
  });
}

function startCollectionPolling() {
  if (state.collectionPollTimer) return;
  stopCollectionAutoRefresh();
  state.collectionPollTimer = window.setInterval(async () => {
    try {
      await refreshUsersTable();
    } catch {
      stopCollectionPolling();
    }
  }, 5000);
  startCollectionTicking();
}

function stopCollectionPolling() {
  if (!state.collectionPollTimer) return;
  window.clearInterval(state.collectionPollTimer);
  state.collectionPollTimer = null;
  stopCollectionTicking();
  updateCollectionAutoRefresh();
}

function startCollectionTicking() {
  if (state.collectionTickTimer) return;
  state.collectionTickTimer = window.setInterval(() => {
    if (!hasCollectionPollingWork()) {
      stopCollectionTicking();
      return;
    }
    updateCollectionElapsedText();
  }, 1000);
}

function updateCollectionElapsedText() {
  document.querySelectorAll("[data-collection-time]").forEach((node) => {
    const userId = node.getAttribute("data-collection-time");
    const user = state.users.find((item) => item.user_id === userId);
    if (user) node.textContent = collectionTimeText(user);
  });
}

function stopCollectionTicking() {
  if (!state.collectionTickTimer) return;
  window.clearInterval(state.collectionTickTimer);
  state.collectionTickTimer = null;
}

function updateCollectionPolling() {
  if (hasCollectionPollingWork()) {
    startCollectionPolling();
  } else {
    stopCollectionPolling();
  }
}

function shouldAutoRefreshCollections() {
  return Boolean(state.accessToken)
    && state.view === "operations"
    && !state.loading
    && !state.collectionPollTimer;
}

function startCollectionAutoRefresh() {
  if (state.collectionAutoRefreshTimer) return;
  state.collectionAutoRefreshTimer = window.setInterval(async () => {
    if (!shouldAutoRefreshCollections()) {
      stopCollectionAutoRefresh();
      return;
    }
    try {
      await refreshUsersTable();
    } catch {
      stopCollectionAutoRefresh();
    }
  }, 30000);
}

function stopCollectionAutoRefresh() {
  if (!state.collectionAutoRefreshTimer) return;
  window.clearInterval(state.collectionAutoRefreshTimer);
  state.collectionAutoRefreshTimer = null;
}

function updateCollectionAutoRefresh() {
  if (shouldAutoRefreshCollections()) {
    startCollectionAutoRefresh();
  } else {
    stopCollectionAutoRefresh();
  }
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
  const isSignup = state.authMode === "signup";
  return `
    <section class="loginWrap">
      <form class="loginCard form" id="${isSignup ? "signupForm" : "loginForm"}">
        ${brandBlock()}
        <div class="authSwitch">
          <button class="${!isSignup ? "active" : ""}" type="button" data-auth-mode="login">로그인</button>
          <button class="${isSignup ? "active" : ""}" type="button" data-auth-mode="signup">회원가입</button>
        </div>
        <label>
          <span class="meta">관리자 이메일</span>
          <input name="email" type="email" autocomplete="username" required />
        </label>
        <label>
          <span class="meta">비밀번호</span>
          <input name="password" type="password" autocomplete="${isSignup ? "new-password" : "current-password"}" required />
        </label>
        ${isSignup ? `
          <label>
            <span class="meta">관리자 가입 코드</span>
            <input name="signupCode" type="password" autocomplete="off" required />
          </label>
        ` : ""}
        ${state.error ? `<p class="${state.error.includes("변경") ? "notice" : "error"}">${state.error}</p>` : ""}
        <button type="submit" ${state.loading ? "disabled" : ""}>${isSignup ? "관리자 회원가입" : "관리자 로그인"}</button>
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
        ${brandBlock()}
        <nav class="nav">
          ${navButton("operations", "운영")}
          ${navButton("account", "내 계정")}
        </nav>
        <div class="sidebarFoot">
          <button class="secondary" id="logoutButton">로그아웃</button>
        </div>
      </aside>
      <section class="main">
        <header class="pageTitle">
          <div>
            <h1>${isAccount ? "내 계정" : "운영"}</h1>
            <p>${isAccount ? "관리자 세션과 계정 작업을 확인합니다." : "사용자 상태와 시스템 상태를 확인합니다."}</p>
          </div>
        </header>
        <div id="messageArea">${messageView()}</div>
        ${isAccount ? accountView() : operationsView()}
      </section>
    </div>
  `;
}

function navButton(view, label) {
  return `<button class="${state.view === view ? "active" : ""}" type="button" data-view="${view}">${navIcons[view] || ""}${label}</button>`;
}

function operationsView() {
  const activeUsers = state.users.filter((user) => user.consent_active).length;
  const inactiveUsers = state.users.length - activeUsers;
  const pendingDeletion = state.users.filter((user) => user.deletion_pending).length;
  const consentRate = state.users.length === 0 ? 0 : Math.round((activeUsers / state.users.length) * 100);
  const healthy = state.health === "정상";
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
      <div class="card">
        <div class="cardHead">
          <h2>운영 체크리스트</h2>
          <span class="status ${healthy && activeUsers > 0 && pendingDeletion === 0 ? "" : "warn"}">${healthy ? "확인" : "점검"}</span>
        </div>
        <div class="checkList">
          ${checkItem("API 응답", healthy ? "정상" : "점검 필요", healthy)}
          ${checkItem("추천 가능 사용자", `${activeUsers}명`, activeUsers > 0)}
          ${checkItem("삭제 대기", `${pendingDeletion}건`, pendingDeletion === 0)}
        </div>
      </div>
    </section>
    <section class="contentStack">
      <div class="card">
        <div class="cardHead">
          <h2>주간 배치 (Discovery)</h2>
        </div>
        <p class="meta">
          UserProfile cron (Discovery slot 의 Fusion/Reincarnation 후보) 을 즉시 실행.
          모든 사용자 순회 · LLM 호출 비용 발생 (rate_limit 5/hour).
        </p>
        <button type="button" data-user-profile-cron>UserProfile cron 실행</button>
      </div>
      <div class="card">
        <div class="cardHead">
          <h2>최근 사용자</h2>
          <button class="iconRefresh" type="button" title="최근 사용자 새로고침" data-refresh>↻</button>
        </div>
        <div id="usersTableMount">${usersTable(state.users.slice(0, 8))}</div>
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
          ${statusRow("권한", "관리자", true)}
          ${statusRow("토큰 만료", session.expiresAt, session.valid)}
        </div>
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

function checkItem(label, value, ok) {
  return `
    <span class="${ok ? "" : "warn"}">
      <b>${label}</b>
      <em>${value}</em>
    </span>
  `;
}

function usersTable(users) {
  if (users.length === 0) return `<p class="muted">표시할 사용자가 없습니다.</p>`;
  return `
    <div class="table">
      <div class="row header">
        <span>이메일</span><span>동의</span><span>삭제 대기</span><span>최근 수집</span><span>가입일</span><span>작업</span>
      </div>
      ${users.map(userRow).join("")}
    </div>
  `;
}

function userRow(user) {
  const busy = state.collectionBusyUserId === user.user_id;
  const inFlight = isCollectionInFlight(user);
  const disabled = !user.consent_active || busy || inFlight;
  const buttonLabel = busy
    ? `${buttonSpinner()}등록 중`
    : (inFlight ? `${buttonSpinner()}진행 중` : "수집 실행");
  return `
    <div class="row">
      <strong>${escapeHtml(user.email)}</strong>
      <span class="status ${user.consent_active ? "" : "warn"}">${user.consent_active ? "활성" : "비활성"}</span>
      <span class="status ${user.deletion_pending ? "danger" : ""}">${user.deletion_pending ? "대기" : "없음"}</span>
      ${collectionMeta(user)}
      <span class="muted">${formatDate(user.created_at)}</span>
      <button
        class="tableAction"
        type="button"
        data-run-collection="${escapeHtml(user.user_id)}"
        data-user-email="${escapeHtml(user.email)}"
        data-in-flight="${inFlight ? "true" : "false"}"
        ${disabled ? "disabled" : ""}
      >${buttonLabel}</button>
    </div>
  `;
}

function buttonSpinner() {
  return `<i class="buttonSpinner" aria-hidden="true"></i>`;
}

function bindLogin() {
  document.querySelector("#loginForm")?.addEventListener("submit", login);
  document.querySelector("#signupForm")?.addEventListener("submit", signup);
  document.querySelectorAll("[data-auth-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      state.authMode = button.getAttribute("data-auth-mode") || "login";
      state.error = "";
      render();
    });
  });
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
      updateCollectionPolling();
      updateCollectionAutoRefresh();
    });
  });
  document.querySelectorAll("[data-refresh]").forEach((button) => {
    button.addEventListener("click", loadDashboard);
  });
  document.querySelector("[data-user-profile-cron]")?.addEventListener("click", (event) => {
    event.preventDefault();
    triggerUserProfileCron();
  });
  bindCollectionButtons();
  document.querySelector("#logoutButton")?.addEventListener("click", logout);
}

function bindCollectionButtons() {
  document.querySelectorAll("[data-run-collection]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      runCollectionForUser(
        button.getAttribute("data-run-collection"),
        button.getAttribute("data-user-email") || ""
      );
    });
  });
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
