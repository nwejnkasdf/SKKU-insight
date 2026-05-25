const apiBase = window.__ADMIN_CONFIG__?.apiBase || "http://localhost:8000";

const state = {
  accessToken: localStorage.getItem("admin_access") || "",
  refreshToken: localStorage.getItem("admin_refresh") || "",
  adminEmail: localStorage.getItem("admin_email") || "admin@skkuinsight.org",
  mustChangePassword: localStorage.getItem("admin_must_change") === "true",
  loading: false,
  error: "",
  notice: "",
  noticeTone: "success",
  collectionBusyUserId: "",
  collectionRowMessages: {},
  collectionPollTimer: null,
  collectionTickTimer: null,
  collectionAutoRefreshTimer: null,
  users: [],
  health: null,
  view: window.location.hash === "#insights"
    ? "insights"
    : window.location.hash === "#account"
      ? "account"
      : "operations",
  // C-61 admin debug console (SUPER 전용)
  adminRole: null,
  insightsTargetInput: "",
  insightsTarget: null,
  insightsLoading: false,
  insightsError: "",
  insightsData: null,
  insightsTab: "trace",
  insightsBusyAction: null,
  simulateMode: "next_day",
  simulateDays: 1,
  simulateStatus: null,
  simulatePollTimer: null,
  systemConfig: null,
  systemConfigBusy: false,
  systemConfigEditing: null,
  systemConfigEditValue: "",
  actionConfirm: null
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
  `,
  insights: `
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3" />
      <path d="M12 19v3" />
      <path d="M2 12h3" />
      <path d="M19 12h3" />
      <path d="M5 5l2 2" />
      <path d="M17 17l2 2" />
      <path d="M5 19l2-2" />
      <path d="M17 7l2-2" />
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
  state.adminRole = null;
  state.insightsData = null;
  state.insightsTarget = null;
  state.insightsTargetInput = "";
  state.simulateStatus = null;
  state.systemConfig = null;
  state.actionConfirm = null;
  stopSimulatePolling();
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
    const [users, health, me] = await Promise.all([
      request("/admin/users?limit=20"),
      loadHealth(),
      request("/admin/auth/me").catch(() => null)
    ]);
    state.users = users.items || [];
    state.health = health;
    state.adminRole = me && typeof me === "object" ? me.role || null : null;
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
  return `
    <section class="loginWrap">
      <form class="loginCard form" id="loginForm">
        ${brandBlock()}
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
  const isInsights = state.view === "insights";
  const isSuper = state.adminRole === "super";
  const showInsights = isInsights && isSuper;
  let title;
  let sub;
  let body;
  if (showInsights) {
    title = "사용자 인사이트";
    sub = "trace · leaf · 추천 · interest raw + 운영 액션 (SUPER 전용 디버그)";
    body = insightsView();
  } else if (isAccount) {
    title = "내 계정";
    sub = "관리자 세션과 계정 작업을 확인합니다.";
    body = accountView();
  } else {
    title = "운영";
    sub = "사용자 상태와 시스템 상태를 확인합니다.";
    body = operationsView();
  }
  return `
    <div class="shell">
      <aside class="sidebar">
        ${brandBlock()}
        <nav class="nav">
          ${navButton("operations", "운영")}
          ${navButton("account", "내 계정")}
          ${isSuper ? navButton("insights", "사용자 인사이트") : ""}
        </nav>
        <div class="sidebarFoot">
          <button class="secondary" id="logoutButton">로그아웃</button>
        </div>
      </aside>
      <section class="main">
        <header class="pageTitle">
          <div>
            <h1>${title}</h1>
            <p>${sub}</p>
          </div>
        </header>
        <div id="messageArea">${messageView()}</div>
        ${body}
      </section>
    </div>
    ${state.actionConfirm ? actionConfirmDialog() : ""}
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
          ${statusRow("이메일", state.adminEmail, true)}
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
}

function bindPasswordChange() {
  document.querySelector("#passwordForm")?.addEventListener("submit", changePassword);
  document.querySelector("#logoutButton")?.addEventListener("click", logout);
}

function bindApp() {
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => {
      state.view = button.getAttribute("data-view") || "operations";
      window.location.hash =
        state.view === "account"
          ? "account"
          : state.view === "insights"
            ? "insights"
            : "operations";
      render();
      updateCollectionPolling();
      updateCollectionAutoRefresh();
      if (state.view === "insights") {
        refreshSimulateStatusIfNeeded();
      } else {
        stopSimulatePolling();
      }
    });
  });
  document.querySelectorAll("[data-refresh]").forEach((button) => {
    button.addEventListener("click", loadDashboard);
  });
  bindCollectionButtons();
  document.querySelector("#logoutButton")?.addEventListener("click", logout);
  if (state.view === "insights") bindInsights();
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

// ============================================================
// C-61 인사이트 view (SUPER 전용 디버그 콘솔)
// ============================================================

function insightsView() {
  const target = state.insightsTarget;
  const data = state.insightsData;
  return `
    <section class="insightsBoard">
      <div class="card">
        <div class="cardHead">
          <h2>대상 사용자</h2>
        </div>
        <div class="insightsSearch">
          <input
            type="text"
            id="insightsTargetInput"
            placeholder="이메일 또는 user_id (UUID)"
            value="${escapeHtml(state.insightsTargetInput)}"
            ${state.insightsLoading ? "disabled" : ""}
          />
          <button type="button" id="insightsLookupButton" ${state.insightsLoading ? "disabled" : ""}>
            ${state.insightsLoading ? `${buttonSpinner()}로드 중` : "인사이트 로드"}
          </button>
          ${
            target
              ? `<span class="muted">${escapeHtml(target.email || "?")} · ${escapeHtml(target.user_id)}</span>`
              : ""
          }
        </div>
        ${state.insightsError ? `<p class="error">${escapeHtml(state.insightsError)}</p>` : ""}
        ${
          target
            ? `<p class="muted">최근 사용자 ${state.users.length}건 안에서 매칭. 다른 사용자는 운영 view 에서 새로고침 후 시도.</p>`
            : ""
        }
      </div>
      ${data ? insightsDomainsCard(target, data) : `<p class="muted">사용자 검색 후 4 도메인 raw 노출.</p>`}
      ${target ? insightsSimulateCard(target) : ""}
      ${insightsSystemConfigCard()}
    </section>
  `;
}

function insightsDomainsCard(target, data) {
  const tabs = [
    ["trace", `Traces (${data.traces.length})`],
    ["leaves", `Leaves (${data.leaves.length})`],
    ["recommendations", `Recommendations (${data.recommendations.length})`],
    ["interest", `Interest (${data.interest.topics.length})`]
  ];
  let body = "";
  if (state.insightsTab === "trace") body = renderTraceTable(target, data.traces);
  else if (state.insightsTab === "leaves") body = renderLeafTable(target, data.leaves);
  else if (state.insightsTab === "recommendations") body = renderRecoTable(target, data.recommendations);
  else if (state.insightsTab === "interest") body = renderInterestTable(target, data.interest);
  return `
    <div class="card">
      <div class="cardHead">
        <h2>4 도메인 raw</h2>
        <button class="iconRefresh" type="button" id="insightsRefreshButton" title="다시 로드">↻</button>
      </div>
      <div class="insightsTabs">
        ${tabs
          .map(
            ([k, label]) =>
              `<button type="button" class="${state.insightsTab === k ? "active" : ""}" data-tab="${k}">${escapeHtml(label)}</button>`
          )
          .join("")}
      </div>
      <div class="insightsTable">
        ${body}
      </div>
    </div>
  `;
}

function renderTraceTable(target, traces) {
  if (!traces.length) return `<p class="muted">trace 없음.</p>`;
  const header = `
    <div class="row header">
      <span>status / path</span>
      <span>started ad</span>
      <span>last ad</span>
      <span>arch ad</span>
      <span>tail score</span>
      <span>leaves</span>
      <span>액션</span>
    </div>
  `;
  const rows = traces
    .map((t) => {
      const canArchive = t.status === "active" || t.status === "stale";
      return `
        <div class="row">
          <span>
            <b class="status ${t.status === "archived" ? "danger" : t.status === "stale" ? "warn" : ""}">${escapeHtml(t.status)}</b>
            <div class="pathChips">${t.path_labels.map((l) => `<span>${escapeHtml(l)}</span>`).join("")}</div>
            <small class="raw">${escapeHtml(t.trace_id)}</small>
          </span>
          <span>${t.started_active_day}</span>
          <span>${t.last_activity_active_day}</span>
          <span>${t.archived_at_active_day ?? "-"}</span>
          <span>${(t.score_tail || 0).toFixed(3)}</span>
          <span>${t.leaf_count}</span>
          <span>
            ${
              canArchive
                ? `<button class="dangerAction" type="button" data-action="retract-trace" data-trace-id="${escapeHtml(t.trace_id)}" data-trace-label="${escapeHtml(t.path_labels.join(" › "))}">강제 종료</button>`
                : `<span class="muted">-</span>`
            }
          </span>
        </div>
      `;
    })
    .join("");
  return header + rows;
}

function renderLeafTable(target, leaves) {
  if (!leaves.length) return `<p class="muted">leaf 없음.</p>`;
  const header = `
    <div class="row header">
      <span>label · status</span>
      <span>confidence</span>
      <span>created ad</span>
      <span>last signal</span>
      <span>cso 매핑</span>
      <span>merged into</span>
      <span>액션</span>
    </div>
  `;
  const rows = leaves
    .map((l) => {
      const canArchive = l.status === "emerging" || l.status === "active";
      return `
        <div class="row">
          <span>
            <b>${escapeHtml(l.label)}</b>
            ${l.label_en ? `<small class="muted"> · ${escapeHtml(l.label_en)}</small>` : ""}
            <b class="status ${l.status === "archived" || l.status === "merged" ? "danger" : l.status === "stale" ? "warn" : ""}">${escapeHtml(l.status)}</b>
            <small class="raw">${escapeHtml(l.leaf_topic_id)}</small>
          </span>
          <span>${(l.confidence || 0).toFixed(3)}</span>
          <span>${l.created_active_day}</span>
          <span>${l.last_signal_active_day}</span>
          <span class="pathChips">${l.cso_mapping_labels.map((c) => `<span>${escapeHtml(c)}</span>`).join("") || `<span class="muted">없음</span>`}</span>
          <span>${l.merged_into_leaf_topic_id ? `<small class="raw">${escapeHtml(l.merged_into_leaf_topic_id)}</small>` : "-"}</span>
          <span>
            ${
              canArchive
                ? `<button class="dangerAction" type="button" data-action="archive-leaf" data-leaf-id="${escapeHtml(l.leaf_topic_id)}" data-leaf-label="${escapeHtml(l.label)}">archive</button>`
                : `<span class="muted">-</span>`
            }
          </span>
        </div>
      `;
    })
    .join("");
  return header + rows;
}

function renderRecoTable(target, recos) {
  const header = `
    <div class="row header">
      <span>title · slot</span>
      <span>score</span>
      <span>reason</span>
      <span>origin_type</span>
      <span>origin_ref</span>
      <span>created_at</span>
      <span></span>
    </div>
  `;
  const rows = recos
    .map(
      (r) => `
        <div class="row">
          <span>
            <b>${escapeHtml(r.document_title)}</b>
            <b class="status">${escapeHtml(r.slot_type)}</b>
            <small class="raw">${escapeHtml(r.recommendation_id)}</small>
          </span>
          <span>${r.score === null || r.score === undefined ? "-" : r.score.toFixed(3)}</span>
          <span>${escapeHtml(r.reason || "-")}</span>
          <span>${escapeHtml(r.origin_type || "-")}</span>
          <span><small class="raw">${escapeHtml(r.origin_ref || "-")}</small></span>
          <span class="muted">${formatDate(r.created_at)}</span>
          <span></span>
        </div>
      `
    )
    .join("");
  const cleanupBtn = `<button class="dangerAction" type="button" id="cleanupPseudoButton">pseudo recommendation 일괄 정리</button>`;
  return (
    `<div style="display:flex;gap:.5rem;justify-content:flex-end;margin-bottom:.5rem">${cleanupBtn}</div>` +
    header +
    (rows || `<p class="muted">recommendation 없음.</p>`)
  );
}

function renderInterestTable(target, interest) {
  if (!interest.topics.length) return `<p class="muted">interest 없음.</p>`;
  const header = `
    <div class="row header">
      <span>label</span>
      <span>bucket</span>
      <span>long_score</span>
      <span>short_score</span>
      <span>cso_id</span>
      <span>leaf_id</span>
      <span></span>
    </div>
  `;
  const rows = interest.topics
    .map(
      (t) => `
        <div class="row">
          <span>${escapeHtml(t.label)}</span>
          <span><b class="status ${t.bucket === "neutral" ? "" : ""}">${escapeHtml(t.bucket)}</b></span>
          <span>${(t.long_score || 0).toFixed(3)}</span>
          <span>${(t.short_score || 0).toFixed(3)}</span>
          <span><small class="raw">${escapeHtml(t.cso_topic_id || "-")}</small></span>
          <span><small class="raw">${escapeHtml(t.leaf_topic_id || "-")}</small></span>
          <span></span>
        </div>
      `
    )
    .join("");
  return header + rows;
}

function insightsSimulateCard(target) {
  const sim = state.simulateStatus;
  const running = sim && (sim.state === "queued" || sim.state === "running");
  return `
    <div class="card">
      <div class="cardHead">
        <h2>시간 시뮬레이션</h2>
        ${sim ? `<span class="status ${sim.state === "failed" ? "danger" : sim.state === "succeeded" ? "" : "warn"}">${escapeHtml(sim.state)}</span>` : ""}
      </div>
      <div class="simulatePanel">
        <div class="simulateControls">
          <select id="simulateMode" ${running ? "disabled" : ""}>
            <option value="next_day" ${state.simulateMode === "next_day" ? "selected" : ""}>next_day (수집 X)</option>
            <option value="full_day" ${state.simulateMode === "full_day" ? "selected" : ""}>full_day (수집 + 평가)</option>
            <option value="weekly" ${state.simulateMode === "weekly" ? "selected" : ""}>weekly (단독 갱신)</option>
          </select>
          <input
            type="number"
            id="simulateDays"
            min="1"
            max="30"
            value="${state.simulateDays}"
            ${running || state.simulateMode === "weekly" ? "disabled" : ""}
          />
          <button type="button" id="simulateStartButton" ${running ? "disabled" : ""}>
            ${running ? `${buttonSpinner()}진행 중` : "시뮬레이션 시작"}
          </button>
        </div>
        ${
          sim
            ? `<div class="simulateProgress">
                <div><b>mode</b>${escapeHtml(sim.mode || "-")}</div>
                <div><b>days</b>${sim.days_done ?? 0} / ${sim.days_total ?? 0}</div>
                <div><b>weekly chains</b>${sim.weekly_chains ?? 0}</div>
                <div><b>started</b><small>${escapeHtml(sim.started_at || "-")}</small></div>
                <div><b>finished</b><small>${escapeHtml(sim.finished_at || "-")}</small></div>
                ${sim.message ? `<div style="grid-column:1/-1"><b>message</b><small>${escapeHtml(sim.message)}</small></div>` : ""}
              </div>`
            : `<p class="muted">아직 실행된 시뮬레이션이 없습니다.</p>`
        }
        <p class="muted">next_day/full_day 가 active_day 를 7 배수로 도달시키면 weekly 자동 chain. days 14 → weekly 2회.</p>
      </div>
    </div>
  `;
}

function insightsSystemConfigCard() {
  const cfg = state.systemConfig;
  return `
    <div class="card">
      <div class="cardHead">
        <h2>system_config</h2>
        <button class="iconRefresh" type="button" id="systemConfigReloadButton" title="다시 로드">↻</button>
      </div>
      ${
        cfg === null
          ? `<p class="muted">아래 새로고침으로 로드.</p>`
          : cfg.items.length === 0
            ? `<p class="muted">row 없음.</p>`
            : `<div class="systemConfigEditor">${cfg.items.map(systemConfigRowView).join("")}</div>`
      }
    </div>
  `;
}

function systemConfigRowView(item) {
  const editing = state.systemConfigEditing === item.key;
  return `
    <div class="systemConfigRow">
      <span>
        <b>${escapeHtml(item.key)}</b>
        <small class="muted">${formatDate(item.updated_at)}</small>
        ${item.description ? `<p class="muted" style="font-size:.78rem">${escapeHtml(item.description)}</p>` : ""}
      </span>
      <span>
        ${
          editing
            ? `<textarea data-system-config-edit="${escapeHtml(item.key)}">${escapeHtml(state.systemConfigEditValue)}</textarea>`
            : `<code>${escapeHtml(JSON.stringify(item.value, null, 2))}</code>`
        }
      </span>
      <span>
        ${
          editing
            ? `<button type="button" data-system-config-save="${escapeHtml(item.key)}" ${state.systemConfigBusy ? "disabled" : ""}>저장</button>
               <button class="secondary" type="button" data-system-config-cancel="1">취소</button>`
            : `<button class="secondary" type="button" data-system-config-edit-key="${escapeHtml(item.key)}">편집</button>`
        }
      </span>
    </div>
  `;
}

function actionConfirmDialog() {
  const c = state.actionConfirm;
  if (!c) return "";
  return `
    <div class="dialogBackdrop">
      <div class="dialogBox">
        <h2>${escapeHtml(c.title)}</h2>
        <p>${escapeHtml(c.message)}</p>
        <textarea id="actionConfirmReason" placeholder="사유 (선택)"></textarea>
        <div class="dialogActions">
          <button type="button" id="actionConfirmCancel">취소</button>
          <button type="button" class="primary" id="actionConfirmOk">${escapeHtml(c.okLabel || "실행")}</button>
        </div>
      </div>
    </div>
  `;
}

function bindInsights() {
  document.querySelector("#insightsTargetInput")?.addEventListener("input", (event) => {
    state.insightsTargetInput = event.currentTarget.value;
  });
  document.querySelector("#insightsLookupButton")?.addEventListener("click", runInsightsLookup);
  document.querySelector("#insightsRefreshButton")?.addEventListener("click", () => {
    if (state.insightsTarget) loadInsightsData(state.insightsTarget);
  });
  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      state.insightsTab = button.getAttribute("data-tab") || "trace";
      render();
    });
  });
  document.querySelectorAll('[data-action="archive-leaf"]').forEach((button) => {
    button.addEventListener("click", () => {
      const leafId = button.getAttribute("data-leaf-id");
      const label = button.getAttribute("data-leaf-label") || leafId;
      if (!leafId) return;
      openConfirm({
        title: "leaf 강제 archive",
        message: `${label} (leaf=${leafId}) 을 강제 archive 처리합니다. 시연 시 다른 액션과 함께 신중히.`,
        okLabel: "archive",
        onConfirm: () => runForceArchiveLeaf(leafId)
      });
    });
  });
  document.querySelectorAll('[data-action="retract-trace"]').forEach((button) => {
    button.addEventListener("click", () => {
      const traceId = button.getAttribute("data-trace-id");
      const label = button.getAttribute("data-trace-label") || traceId;
      if (!traceId) return;
      openConfirm({
        title: "trace 강제 종료",
        message: `${label} (trace=${traceId}) 를 archive 처리합니다 (path.pop 없음).`,
        okLabel: "강제 종료",
        onConfirm: () => runForceRetractTrace(traceId)
      });
    });
  });
  document.querySelector("#cleanupPseudoButton")?.addEventListener("click", () => {
    openConfirm({
      title: "pseudo recommendation 정리",
      message: "현재 사용자의 pseudo_cold_start 추천 row 를 일괄 DELETE 합니다.",
      okLabel: "정리",
      onConfirm: runCleanupPseudo
    });
  });
  document.querySelector("#simulateMode")?.addEventListener("change", (event) => {
    state.simulateMode = event.currentTarget.value;
    render();
  });
  document.querySelector("#simulateDays")?.addEventListener("change", (event) => {
    const v = Number(event.currentTarget.value);
    state.simulateDays = Number.isFinite(v) && v >= 1 ? Math.min(Math.floor(v), 30) : 1;
  });
  document.querySelector("#simulateStartButton")?.addEventListener("click", runSimulateStart);
  document.querySelector("#systemConfigReloadButton")?.addEventListener("click", loadSystemConfig);
  document.querySelectorAll("[data-system-config-edit-key]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.getAttribute("data-system-config-edit-key");
      const item = state.systemConfig?.items.find((i) => i.key === key);
      if (!item) return;
      state.systemConfigEditing = key;
      state.systemConfigEditValue = JSON.stringify(item.value, null, 2);
      render();
    });
  });
  document.querySelector("[data-system-config-cancel]")?.addEventListener("click", () => {
    state.systemConfigEditing = null;
    state.systemConfigEditValue = "";
    render();
  });
  document.querySelectorAll("[data-system-config-edit]").forEach((textarea) => {
    textarea.addEventListener("input", (event) => {
      state.systemConfigEditValue = event.currentTarget.value;
    });
  });
  document.querySelectorAll("[data-system-config-save]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.getAttribute("data-system-config-save");
      if (key) runUpdateSystemConfig(key);
    });
  });
  document.querySelector("#actionConfirmCancel")?.addEventListener("click", closeConfirm);
  document.querySelector("#actionConfirmOk")?.addEventListener("click", () => {
    const c = state.actionConfirm;
    if (!c) return;
    const reasonNode = document.querySelector("#actionConfirmReason");
    const reason = reasonNode ? reasonNode.value : "";
    closeConfirm();
    if (typeof c.onConfirm === "function") c.onConfirm(reason);
  });
}

function openConfirm(opts) {
  state.actionConfirm = opts;
  render();
}

function closeConfirm() {
  state.actionConfirm = null;
  render();
}

function resolveInsightsTarget(input) {
  const trimmed = (input || "").trim();
  if (!trimmed) return { ok: false, error: "이메일 또는 user_id 를 입력하세요." };
  // UUID 형태인지 검사 (정규식).
  const uuidRe = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (uuidRe.test(trimmed)) {
    const match = state.users.find((u) => u.user_id === trimmed);
    return {
      ok: true,
      target: { user_id: trimmed, email: match ? match.email : null }
    };
  }
  // email 매칭 — 운영 view 의 최근 사용자 20명 안에서.
  const lower = trimmed.toLowerCase();
  const match = state.users.find((u) => (u.email || "").toLowerCase() === lower);
  if (!match) {
    return {
      ok: false,
      error: `'${trimmed}' 사용자를 최근 20명 안에서 못 찾음. 운영 view 새로고침 또는 user_id 직접 입력.`
    };
  }
  return { ok: true, target: { user_id: match.user_id, email: match.email } };
}

async function runInsightsLookup() {
  const resolved = resolveInsightsTarget(state.insightsTargetInput);
  if (!resolved.ok) {
    state.insightsError = resolved.error || "사용자를 찾지 못했습니다.";
    state.insightsTarget = null;
    state.insightsData = null;
    render();
    return;
  }
  state.insightsError = "";
  state.insightsTarget = resolved.target;
  await loadInsightsData(resolved.target);
}

async function loadInsightsData(target) {
  state.insightsLoading = true;
  render();
  try {
    const [traces, leaves, recommendations, interest] = await Promise.all([
      request(`/admin/users/${encodeURIComponent(target.user_id)}/traces`),
      request(`/admin/users/${encodeURIComponent(target.user_id)}/leaves`),
      request(`/admin/users/${encodeURIComponent(target.user_id)}/recommendations`),
      request(`/admin/users/${encodeURIComponent(target.user_id)}/interest-state`)
    ]);
    state.insightsData = {
      traces: Array.isArray(traces) ? traces : [],
      leaves: Array.isArray(leaves) ? leaves : [],
      recommendations: Array.isArray(recommendations) ? recommendations : [],
      interest: interest || { topics: [], updated_at: null }
    };
    state.insightsError = "";
    // simulate status 도 같이 갱신 (이미 큐잉돼 있다면 진행률 보임).
    refreshSimulateStatusIfNeeded();
  } catch (error) {
    state.insightsError = messageForError(error);
  } finally {
    state.insightsLoading = false;
    render();
  }
}

async function runForceArchiveLeaf(leafId) {
  if (!state.insightsTarget) return;
  state.insightsBusyAction = `archive-leaf:${leafId}`;
  render();
  try {
    await request(`/admin/users/${encodeURIComponent(state.insightsTarget.user_id)}/leaves/${encodeURIComponent(leafId)}/archive`, {
      method: "POST",
      body: JSON.stringify({ reason: "" })
    });
    setMessage("leaf archive 완료. 인사이트 다시 로드.", "success");
    await loadInsightsData(state.insightsTarget);
  } catch (error) {
    setMessage(messageForError(error), "warn");
  } finally {
    state.insightsBusyAction = null;
    render();
  }
}

async function runForceRetractTrace(traceId) {
  if (!state.insightsTarget) return;
  state.insightsBusyAction = `retract-trace:${traceId}`;
  render();
  try {
    await request(`/admin/users/${encodeURIComponent(state.insightsTarget.user_id)}/traces/${encodeURIComponent(traceId)}/retract`, {
      method: "POST",
      body: JSON.stringify({ reason: "" })
    });
    setMessage("trace 강제 종료 완료.", "success");
    await loadInsightsData(state.insightsTarget);
  } catch (error) {
    setMessage(messageForError(error), "warn");
  } finally {
    state.insightsBusyAction = null;
    render();
  }
}

async function runCleanupPseudo() {
  if (!state.insightsTarget) return;
  try {
    const res = await request(`/admin/users/${encodeURIComponent(state.insightsTarget.user_id)}/recommendations/cleanup-pseudo`, {
      method: "POST"
    });
    setMessage(`pseudo recommendation ${res?.deleted_count ?? 0}건 삭제.`, "success");
    await loadInsightsData(state.insightsTarget);
  } catch (error) {
    setMessage(messageForError(error), "warn");
  }
}

async function runSimulateStart() {
  if (!state.insightsTarget) {
    setMessage("대상 사용자 먼저 검색.", "warn");
    return;
  }
  const days = state.simulateMode === "weekly" ? 1 : Math.max(1, Math.min(30, state.simulateDays || 1));
  try {
    await request(`/admin/users/${encodeURIComponent(state.insightsTarget.user_id)}/simulate`, {
      method: "POST",
      body: JSON.stringify({ mode: state.simulateMode, days })
    });
    setMessage("시뮬레이션 큐잉 완료. worker 가 곧 시작합니다.", "success");
    startSimulatePolling();
    refreshSimulateStatus();
  } catch (error) {
    setMessage(messageForError(error), "warn");
  }
}

function startSimulatePolling() {
  if (state.simulatePollTimer) return;
  state.simulatePollTimer = window.setInterval(refreshSimulateStatus, 5000);
}

function stopSimulatePolling() {
  if (!state.simulatePollTimer) return;
  window.clearInterval(state.simulatePollTimer);
  state.simulatePollTimer = null;
}

async function refreshSimulateStatus() {
  if (!state.insightsTarget) {
    stopSimulatePolling();
    return;
  }
  try {
    const res = await request(`/admin/users/${encodeURIComponent(state.insightsTarget.user_id)}/simulate/status`);
    state.simulateStatus = res || null;
    const finished = !res || res.state === "idle" || res.state === "succeeded" || res.state === "failed";
    if (finished) {
      stopSimulatePolling();
      if (res && (res.state === "succeeded" || res.state === "failed")) {
        // succeeded → 인사이트 자동 reload.
        if (res.state === "succeeded") await loadInsightsData(state.insightsTarget);
      }
    }
    render();
  } catch (error) {
    // 폴링 에러는 silently 무시 (다음 tick 재시도).
  }
}

function refreshSimulateStatusIfNeeded() {
  if (!state.insightsTarget) return;
  refreshSimulateStatus();
  // running 상태면 polling 유지.
  if (state.simulateStatus && (state.simulateStatus.state === "queued" || state.simulateStatus.state === "running")) {
    startSimulatePolling();
  }
}

async function loadSystemConfig() {
  state.systemConfigBusy = true;
  render();
  try {
    const res = await request("/admin/system-config");
    state.systemConfig = res || { items: [] };
  } catch (error) {
    setMessage(messageForError(error), "warn");
  } finally {
    state.systemConfigBusy = false;
    render();
  }
}

async function runUpdateSystemConfig(key) {
  let parsed;
  try {
    parsed = JSON.parse(state.systemConfigEditValue);
  } catch (error) {
    setMessage("JSON 파싱 실패: " + (error.message || error), "warn");
    return;
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    setMessage("JSON object 만 허용 (배열/null/primitive X).", "warn");
    return;
  }
  state.systemConfigBusy = true;
  render();
  try {
    await request(`/admin/system-config/${encodeURIComponent(key)}`, {
      method: "PUT",
      body: JSON.stringify({ value: parsed })
    });
    setMessage(`system_config '${key}' 갱신 + 캐시 invalidate.`, "success");
    state.systemConfigEditing = null;
    state.systemConfigEditValue = "";
    await loadSystemConfig();
  } catch (error) {
    setMessage(messageForError(error), "warn");
  } finally {
    state.systemConfigBusy = false;
    render();
  }
}

if (state.accessToken && !state.mustChangePassword) {
  loadDashboard();
} else {
  render();
}
