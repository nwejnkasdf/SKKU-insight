import {
  Activity,
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Bookmark,
  CheckCircle2,
  EyeOff,
  ExternalLink,
  GitBranch,
  HeartCrack,
  Library,
  ListOrdered,
  Loader2,
  LogOut,
  Network,
  RefreshCcw,
  Settings,
  ShieldCheck,
  TrendingUp,
  Trash2
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type {
  ApiError,
  CSOCluster,
  DashboardResponse,
  DocumentDetailResponse,
  DocumentSummary,
  DocumentSummaryResponse,
  InsightApi,
  InterestStateResponse,
  MeResponse,
  RecommendationCard,
  TopicDocumentsResponse,
  TraversalTraceSummary,
  UUID
} from "./generated/api";
import { getApi, tokenStore } from "./lib/api";
import { configureDwellTracker, startDwell, stopDwell } from "./lib/dwellTracker";

type View =
  | { name: "boot" }
  | { name: "auth" }
  | { name: "onboarding"; me: MeResponse | null }
  | { name: "dashboard" }
  | { name: "topics" }
  | { name: "ranking" }
  | { name: "library" }
  | { name: "document"; documentId: UUID }
  | { name: "topic"; topicId: UUID; label: string }
  | { name: "settings" };

type Toast = { tone: "ok" | "error"; text: string } | null;
type TopicRank = { topicId: UUID; label: string; count: number; rawLabel?: string };
type FeedbackAction = "save" | "hide" | "not_interested";
type FeedbackState = { saved: boolean; hidden: boolean; notInterested: boolean };
type DisplayLabel = { label: string; rawLabel: string };
type ModelNode = { label: string; tone: string; meta?: string; rawLabel?: string; badge?: string };
type ModelLayer = { key: string; title: string; kicker: string; nodes: ModelNode[] };
type DisplayTopic<T extends { label: string }> = T & { rawLabel: string };
type SlotSummaryItem = DashboardResponse["slots"][number];

const baseSlotTargets = {
  core: 5,
  adjacent: 3,
  discovery: 2
} as const;

const slotOrder = ["core", "adjacent", "discovery", "fallback_adjacent", "fallback_trend"];

const userClassOptions = [
  { value: "general", label: "일반" },
  { value: "undergraduate", label: "학부생" },
  { value: "researcher", label: "연구자" },
  { value: "professor", label: "교수" }
];

export default function App() {
  const [api, setApi] = useState<InsightApi | null>(null);
  const [view, setView] = useState<View>({ name: "boot" });
  const [toast, setToast] = useState<Toast>(null);

  useEffect(() => {
    void getApi().then((resolved) => {
      setApi(resolved);
      configureDwellTracker(resolved);
      void resolved
        .me()
        .then((me) => {
          if (!me.consent_active || !me.onboarding_complete) {
            setView({ name: "onboarding", me });
          } else {
            setView({ name: "dashboard" });
          }
        })
        .catch(() => setView({ name: "auth" }));
    });
  }, []);

  function showToast(next: Toast) {
    setToast(next);
    if (next) {
      window.setTimeout(() => setToast(null), 3400);
    }
  }

  if (!api || view.name === "boot") {
    return <Shell view={view} setView={setView} toast={toast}><Loading label="앱을 준비하고 있습니다" /></Shell>;
  }

  return (
    <Shell view={view} setView={setView} toast={toast}>
      {view.name === "auth" && <AuthView api={api} setView={setView} showToast={showToast} />}
      {view.name === "onboarding" && (
        <OnboardingView api={api} me={view.me} setView={setView} showToast={showToast} />
      )}
      {view.name === "dashboard" && (
        <DashboardView api={api} setView={setView} showToast={showToast} />
      )}
      {view.name === "topics" && <TopicsView api={api} setView={setView} />}
      {view.name === "ranking" && <RankingView api={api} setView={setView} />}
      {view.name === "library" && <LibraryView api={api} setView={setView} />}
      {view.name === "document" && (
        <DocumentView api={api} documentId={view.documentId} setView={setView} showToast={showToast} />
      )}
      {view.name === "topic" && (
        <TopicView api={api} topicId={view.topicId} label={view.label} setView={setView} />
      )}
      {view.name === "settings" && <SettingsPanel api={api} setView={setView} showToast={showToast} />}
    </Shell>
  );
}

function Shell({
  children,
  view,
  setView,
  toast
}: {
  children: React.ReactNode;
  view: View;
  setView: (view: View) => void;
  toast: Toast;
}) {
  const authed = !["boot", "auth", "onboarding"].includes(view.name);
  if (!authed) {
    return (
      <div className="entryApp">
        <main className="entryMain">{children}</main>
        {toast && <div className={`toast ${toast.tone}`}>{toast.text}</div>}
      </div>
    );
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brandMark"><GitBranch size={19} /></div>
          <div>
            <strong>SKKU InSight</strong>
            <span>CS/AI 동향 추천</span>
          </div>
        </div>
        <nav>
          <button className={view.name === "dashboard" ? "active" : ""} disabled={!authed} onClick={() => setView({ name: "dashboard" })}>
            <BarChart3 size={17} /> 추천
          </button>
          <button className={view.name === "topics" || view.name === "topic" ? "active" : ""} disabled={!authed} onClick={() => setView({ name: "topics" })}>
            <Network size={17} /> 토픽
          </button>
          <button className={view.name === "ranking" ? "active" : ""} disabled={!authed} onClick={() => setView({ name: "ranking" })}>
            <ListOrdered size={17} /> 랭킹
          </button>
          <button className={view.name === "library" ? "active" : ""} disabled={!authed} onClick={() => setView({ name: "library" })}>
            <Library size={17} /> 보관함
          </button>
          <button className={view.name === "settings" ? "active" : ""} disabled={!authed} onClick={() => setView({ name: "settings" })}>
            <Settings size={17} /> 설정
          </button>
        </nav>
        <div className="sidebarFoot">데모 클라이언트</div>
      </aside>
      <main className="main">{children}</main>
      {toast && <div className={`toast ${toast.tone}`}>{toast.text}</div>}
    </div>
  );
}

function AuthView({
  api,
  setView,
  showToast
}: {
  api: InsightApi;
  setView: (view: View) => void;
  showToast: (toast: Toast) => void;
}) {
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    const normalizedEmail = email.trim().toLowerCase();
    const validationError = mode === "signup" ? validateSignupForm(normalizedEmail, password) : validateLoginForm(normalizedEmail, password);
    if (validationError) {
      setError(validationError);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (mode === "signup") {
        await api.signup(normalizedEmail, password);
      }
      const tokens = await api.login(normalizedEmail, password);
      await tokenStore.setTokens(tokens);
      const me = await api.me();
      setView(!me.consent_active || !me.onboarding_complete ? { name: "onboarding", me } : { name: "dashboard" });
    } catch (err) {
      setError(messageForError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="auth">
      <div className="authCopy">
        <p className="eyebrow">데스크톱 클라이언트</p>
        <h1>검색하지 않아도 따라오는 CS/AI 기술 흐름</h1>
        <p>관심 클러스터와 행동 신호를 바탕으로 오늘 읽을 만한 논문, 블로그, 기술 글을 모읍니다.</p>
        <div className="authPreview">
          <div className="previewHeader">
            <span>오늘의 브리프</span>
            <b>10건</b>
          </div>
          <div className="previewBars">
            <span style={{ width: "72%" }} />
            <span style={{ width: "48%" }} />
            <span style={{ width: "34%" }} />
          </div>
          <div className="previewGrid">
            <span><b>5</b> 중심</span>
            <span><b>3</b> 인접</span>
            <span><b>2</b> 탐색</span>
          </div>
        </div>
      </div>
      <form className="panel authPanel" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
        <div className="segmented">
          <button type="button" className={mode === "login" ? "selected" : ""} onClick={() => setMode("login")}>로그인</button>
          <button type="button" className={mode === "signup" ? "selected" : ""} onClick={() => setMode("signup")}>가입</button>
        </div>
        <label>이메일<input value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@skku.edu" /></label>
        <label>비밀번호<input value={password} onChange={(event) => setPassword(event.target.value)} placeholder="12자 이상" type="password" /></label>
        {error && <p className="inlineError">{error}</p>}
        <button className="primary" disabled={busy || !email || !password}>
          {busy ? <Loader2 className="spin" size={17} /> : <ShieldCheck size={17} />}
          {mode === "login" ? "로그인" : "가입하고 시작"}
        </button>
        <button type="button" className="ghost" onClick={() => showToast({ tone: "ok", text: "백엔드 API가 켜져 있으면 바로 연결됩니다." })}>
          연결 상태 안내
        </button>
      </form>
    </section>
  );
}

function OnboardingView({
  api,
  me,
  setView,
  showToast
}: {
  api: InsightApi;
  me: MeResponse | null;
  setView: (view: View) => void;
  showToast: (toast: Toast) => void;
}) {
  const [consentActive, setConsentActive] = useState(me?.consent_active ?? false);
  const [consentChecked, setConsentChecked] = useState(false);
  const [clusters, setClusters] = useState<CSOCluster[]>([]);
  const [selected, setSelected] = useState<Set<UUID>>(new Set());
  const [userClass, setUserClass] = useState("general");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!consentActive) return;
    void api.clusters().then((res) => setClusters(res.clusters)).catch((err) => setError(messageForError(err)));
  }, [api, consentActive]);

  async function agree() {
    if (!consentChecked) return;
    setBusy(true);
    try {
      await api.consentAgree();
      setConsentActive(true);
      showToast({ tone: "ok", text: "동의가 저장되었습니다." });
    } catch (err) {
      setError(messageForError(err));
    } finally {
      setBusy(false);
    }
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.submitInterests([...selected], userClass);
      await pollColdStart(api, res.request_id);
      setView({ name: "dashboard" });
    } catch (err) {
      setError(messageForError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <Header title="시작 설정" subtitle="관심 클러스터를 고르면 첫 관심 경로와 초기 추천 큐가 만들어집니다." />
      {!consentActive ? (
        <div className="panel consentPanel">
          <h2>개인화 추천 동의</h2>
          <p>추천을 위해 클릭, 저장, 숨김 같은 앱 내 행동을 개인화 신호로 사용합니다.</p>
          <label className="checkLine">
            <input type="checkbox" checked={consentChecked} onChange={(event) => setConsentChecked(event.target.checked)} />
            개인화 추천 처리에 동의합니다.
          </label>
          <button className="primary" disabled={!consentChecked || busy} onClick={() => void agree()}>
            <ShieldCheck size={17} /> 동의하고 계속
          </button>
        </div>
      ) : (
        <div className="onboardingLayout">
          <div className="onboardingMain">
            <div className="onboardingTop">
              <div className="stepPills">
                <span className="current">1. 관심 분야</span>
                <span>2. 초기 경로</span>
                <span>3. 첫 추천</span>
              </div>
              <div className="onboardingTitleRow">
                <div>
                  <h2>관심 분야 선택</h2>
                  <p>넓은 분야를 고르면 첫 추천의 기준점으로만 사용됩니다. 이후에는 클릭, 저장, 숨김 같은 실제 행동이 더 크게 반영됩니다.</p>
                </div>
                <div className="selectedMini">
                  <strong>{selected.size}</strong>
                  <span>개 선택됨</span>
                </div>
              </div>
            </div>
            {error && <p className="inlineError">{error}</p>}
            <div className="clusterGrid">
              {clusters.map((cluster, index) => {
                const isSelected = selected.has(cluster.cso_topic_id);
                return (
                  <button
                    key={cluster.cso_topic_id}
                    className={isSelected ? "cluster selected" : "cluster"}
                    onClick={() => toggleSet(selected, setSelected, cluster.cso_topic_id)}
                  >
                    <div className="clusterTop">
                      <span className="clusterIndex">{String(index + 1).padStart(2, "0")}</span>
                      {isSelected ? <CheckCircle2 size={18} /> : <span className="clusterType">CSO</span>}
                    </div>
                    <strong>{cluster.label}</strong>
                    <span>{cluster.description_ko}</span>
                    <div className="clusterFooter">
                      <small>최근 문서 {cluster.document_count}건</small>
                      <div className="clusterSignal" aria-hidden="true">
                        <i style={{ width: `${Math.min(96, 34 + cluster.document_count)}%` }} />
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
          <div className="onboardingRail">
            <div className="insightPanel">
              <PanelHeading icon={<ShieldCheck size={16} />} title="프로필" />
              <label>사용자 유형
                <select value={userClass} onChange={(event) => setUserClass(event.target.value)}>
                  {userClassOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
              </label>
            </div>
            <SelectionPreview clusters={clusters.filter((cluster) => selected.has(cluster.cso_topic_id))} />
            <div className="insightPanel">
              <PanelHeading icon={<BarChart3 size={16} />} title="첫 추천 구성" />
              <div className="previewSlots">
                <span><b>5</b> 중심</span>
                <span><b>3</b> 인접</span>
                <span><b>2</b> 탐색</span>
              </div>
              <p className="panelNote">선택한 분야는 첫 추천의 기준 신호로 쓰이고, 이후 이벤트가 관심 경로를 갱신합니다.</p>
              <button className="primary wide" disabled={selected.size === 0 || busy} onClick={() => void submit()}>
                {busy ? <Loader2 className="spin" size={17} /> : <CheckCircle2 size={17} />} 추천 준비
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function SelectionPreview({ clusters }: { clusters: CSOCluster[] }) {
  return (
    <div className="insightPanel">
      <PanelHeading icon={<GitBranch size={16} />} title="선택 요약" />
      {clusters.length === 0 ? (
        <p className="panelNote">관심 분야를 선택하면 여기에서 한 번에 확인할 수 있습니다.</p>
      ) : (
        <div className="seedList">
          {clusters.slice(0, 5).map((cluster) => (
            <span key={cluster.cso_topic_id}>{cluster.label}</span>
          ))}
          {clusters.length > 5 && <span>+{clusters.length - 5}개 더</span>}
        </div>
      )}
    </div>
  );
}

function DashboardView({
  api,
  setView,
  showToast
}: {
  api: InsightApi;
  setView: (view: View) => void;
  showToast: (toast: Toast) => void;
}) {
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null);
  const [interest, setInterest] = useState<InterestStateResponse | null>(null);
  const [traces, setTraces] = useState<TraversalTraceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const topicRanks = useMemo(() => dashboard ? buildTopicRanks(dashboard.cards) : [], [dashboard]);

  async function load(refresh = false, silent = false) {
    if (!silent) {
      setLoading(true);
      setError(null);
    }
    try {
      const nextDashboard = refresh ? await api.refreshDashboard() : await api.dashboard();
      const [nextInterest, nextTraces] = await Promise.all([
        api.interestState().catch(() => null),
        api.traces().catch(() => null)
      ]);
      setDashboard(nextDashboard);
      setInterest(nextInterest);
      setTraces(nextTraces?.items ?? []);
    } catch (err) {
      if (silent) {
        showToast({ tone: "error", text: messageForError(err) });
      } else {
        setDashboard(null);
        setError(messageForError(err));
      }
    } finally {
      if (!silent) {
        setLoading(false);
      }
    }
  }

  useEffect(() => {
    void load();
  }, []);

  return (
    <section>
      <Header title="오늘의 추천" subtitle="중심 · 인접 · 탐색 큐">
        <button className="iconButton" title="새로고침" onClick={() => void load(true)}><RefreshCcw size={17} /></button>
      </Header>
      {loading && <Loading label="추천을 불러오는 중" />}
      {!loading && error && <Empty title="추천 엔진을 기다리는 중입니다" body={error} />}
      {!loading && dashboard && (
        <div className="dashboardStack">
          <SignalOverview dashboard={dashboard} topicCount={topicRanks.length} interest={interest} traces={traces} />
          <InterestStructurePanel dashboard={dashboard} interest={interest} traces={traces} />
          <div className="dashboardMain">
            <div className="sectionTitle">
              <div>
                <span>추천 큐</span>
                <h2>추천 우선순위</h2>
              </div>
              <small>{dashboard.cache === "hit" ? "캐시 응답" : "새로 계산됨"} · {formatDate(dashboard.generated_at)}</small>
            </div>
            <div className="dashboardGrid">
              {dashboard.cards.map((card, index) => (
                <RecommendationCardView
                  key={card.recommendation_id}
                  api={api}
                  card={card}
                  rank={index + 1}
                  setView={setView}
                  showToast={showToast}
                  onFeedbackApplied={(action, documentId) => {
                    if (action !== "hide") return;
                    setDashboard((current) => current ? {
                      ...current,
                      cards: current.cards.filter((item) => item.document_id !== documentId)
                    } : current);
                    void load(false, true);
                  }}
                />
              ))}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function RecommendationCardView({
  api,
  card,
  rank,
  setView,
  showToast,
  onFeedbackApplied
}: {
  api: InsightApi;
  card: RecommendationCard;
  rank: number;
  setView: (view: View) => void;
  showToast: (toast: Toast) => void;
  onFeedbackApplied?: (action: FeedbackAction, documentId: UUID) => void;
}) {
  const displayTopics = card.related_topics
    .map(displayTopicChip);
  const visibleTopics = displayTopics.slice(0, 2);
  const hiddenTopicCount = Math.max(0, displayTopics.length - visibleTopics.length);
  const [feedback, setFeedback] = useState<FeedbackState>({
    saved: card.saved,
    hidden: card.hidden,
    notInterested: card.not_interested
  });
  const [busyAction, setBusyAction] = useState<FeedbackAction | null>(null);

  useEffect(() => {
    setFeedback({
      saved: card.saved,
      hidden: card.hidden,
      notInterested: card.not_interested
    });
  }, [card.document_id, card.saved, card.hidden, card.not_interested]);

  async function eventAndOpen() {
    await api.postEvent({
      event_type: "click",
      document_id: card.document_id,
      occurred_at: new Date().toISOString(),
      client_request_id: crypto.randomUUID()
    }).catch(() => undefined);
    setView({ name: "document", documentId: card.document_id });
  }

  async function applyFeedback(action: FeedbackAction) {
    const previous = feedback;
    const active =
      action === "save" ? feedback.saved :
      action === "hide" ? feedback.hidden :
      feedback.notInterested;
    setBusyAction(action);
    setFeedback({
      saved: action === "save" ? !feedback.saved : feedback.saved,
      hidden: action === "hide" ? !feedback.hidden : feedback.hidden,
      notInterested: action === "not_interested" ? !feedback.notInterested : feedback.notInterested
    });

    try {
      if (action === "save") {
        if (active) {
          await api.deleteSaved(card.document_id);
          showToast({ tone: "ok", text: "저장을 해제했습니다." });
        } else {
          await api.saveDocument(card.document_id);
          showToast({ tone: "ok", text: "저장했습니다." });
        }
      } else if (action === "hide") {
        if (active) {
          await api.deleteHidden(card.document_id);
          showToast({ tone: "ok", text: "숨김을 해제했습니다." });
        } else {
          await api.hideDocument(card.document_id);
          showToast({ tone: "ok", text: "숨김 처리했습니다." });
          onFeedbackApplied?.(action, card.document_id);
        }
      } else {
        if (active) {
          await api.deleteNotInterested(card.document_id);
          showToast({ tone: "ok", text: "관심 없음 표시를 해제했습니다." });
        } else {
          await api.notInterestedDocument(card.document_id);
          showToast({ tone: "ok", text: "관심 없음으로 반영했습니다." });
          onFeedbackApplied?.(action, card.document_id);
        }
      }
    } catch (err) {
      setFeedback(previous);
      showToast({ tone: "error", text: messageForError(err) });
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <article className={`recCard recCard-${card.slot_type}`}>
      <div className={`cardGlyph ${card.slot_type}`} aria-hidden="true">
        <b>{String(rank).padStart(2, "0")}</b>
        <span>
          <i />
          <i />
          <i />
        </span>
      </div>
      <button className="cardHit" onClick={() => void eventAndOpen()}>
        <div className="cardMeta">
          <span className={`slot ${card.slot_type}`}>{slotLabel(card.slot_type)}</span>
        </div>
        <h2>{card.title}</h2>
        <span className="sourceLine">{card.source_name} · {formatPublishedDate(card.published_at, card.source_name)}</span>
        <span className={`cardSignal ${card.slot_type}`} aria-hidden="true">
          <i style={{ width: `${Math.max(28, 96 - rank * 7)}%` }} />
        </span>
      </button>
      <div className="chips">
        {visibleTopics.map((topic) => (
          <button key={`${topic.type}-${topic.topic_id}`} title={topic.rawLabel} onClick={() => setView({ name: "topic", topicId: topic.topic_id, label: topic.label })}>{topic.label}</button>
        ))}
        {hiddenTopicCount > 0 && <span className="chipMore">+{hiddenTopicCount}</span>}
      </div>
      <div className="cardActions">
        <button className={feedback.saved ? "isActive" : ""} aria-pressed={feedback.saved} title={feedback.saved ? "저장됨" : "저장"} disabled={busyAction !== null} onClick={() => void applyFeedback("save")}>
          {feedback.saved ? <CheckCircle2 size={16} /> : <Bookmark size={16} />}
        </button>
        <button className={feedback.hidden ? "isActive" : ""} aria-pressed={feedback.hidden} title={feedback.hidden ? "숨김됨" : "숨김"} disabled={busyAction !== null} onClick={() => void applyFeedback("hide")}>
          {feedback.hidden ? <CheckCircle2 size={16} /> : <EyeOff size={16} />}
        </button>
        <button className={feedback.notInterested ? "isActive danger" : ""} aria-pressed={feedback.notInterested} title={feedback.notInterested ? "관심 없음 반영됨" : "관심 없음"} disabled={busyAction !== null} onClick={() => void applyFeedback("not_interested")}>
          {feedback.notInterested ? <CheckCircle2 size={16} /> : <HeartCrack size={16} />}
        </button>
      </div>
    </article>
  );
}

function SignalOverview({
  dashboard,
  topicCount,
  interest,
  traces
}: {
  dashboard: DashboardResponse;
  topicCount: number;
  interest: InterestStateResponse | null;
  traces: TraversalTraceSummary[];
}) {
  const visibleSlots = buildVisibleSlots(dashboard.slots, true, dashboard.cards);
  const maxSlotCount = Math.max(1, ...visibleSlots.map((slot) => slot.actual_count));
  const visibleInterestTopics = (interest?.topics ?? [])
    .map(displayTopicChip);
  const trackedTopics = visibleInterestTopics.filter((topic) => topic.bucket !== "neutral");
  const trackedNodes = buildTrackedNodeItems(dashboard, interest, traces);
  const mapNodes = trackedNodes.slice(0, 5);
  const mapPoints = mapNodes.map((_, index) => signalMapPoint(index, mapNodes.length));
  const activeTraceCount = traces.filter((trace) => trace.status === "active").length;
  return (
    <div className="signalOverview">
      <div className="signalMap" aria-label="추적 관심사 그래프">
        <svg className="signalMapLines" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          {mapNodes.slice(1).map((node, index) => {
            const from = mapPoints[index];
            const to = mapPoints[index + 1];
            return <line key={`line-${node.rawLabel}-${index}`} x1={from.x} y1={from.y} x2={to.x} y2={to.y} />;
          })}
        </svg>
        {mapNodes.map((node, index) => (
          <span
            key={`${node.rawLabel}-${index}`}
            className={`mapNode ${index === mapNodes.length - 1 ? "active" : ""}`}
            style={{ left: `${mapPoints[index].x}%`, top: `${mapPoints[index].y}%` }}
            title={node.rawLabel}
            data-full-label={node.rawLabel}
          >
            {compactTopicLabel(node.label)}
          </span>
        ))}
      </div>
      <div className="slotColumns" aria-label="추천 슬롯 분포">
        {visibleSlots.map((slot) => (
          <span
            key={slot.slot_type}
            className={`${slot.slot_type} ${slot.actual_count === 0 ? "empty" : ""}`}
            title={`${slotLabel(slot.slot_type)} ${slotMeta(slot.actual_count, slot.target_count)}`}
            style={{ height: `${Math.max(18, (slot.actual_count / maxSlotCount) * 64)}px` }}
          >
            <b>{slotLabel(slot.slot_type)}</b>
            <em>{slotMeta(slot.actual_count, slot.target_count)}</em>
          </span>
        ))}
      </div>
      <div className="trackedPanel">
        <span className="panelMiniLabel">추적 관심사</span>
        <div className="trackedChips">
          {(trackedTopics.length ? trackedTopics : visibleInterestTopics.slice(0, 3)).slice(0, 5).map((topic) => (
            <span key={`${topic.cso_topic_id ?? topic.leaf_topic_id}-${topic.rawLabel}`} className={topic.bucket} title={topic.rawLabel}>{topic.label}</span>
          ))}
        </div>
        <small>{activeTraceCount > 0 ? `${activeTraceCount}개 trace 활성` : "trace 생성 대기"} · {interest?.updated_at ? formatDate(interest.updated_at) : "초기 상태"}</small>
      </div>
      <div className="signalStats">
        <span title="현재 추천 큐에 표시되는 문서 수"><b>{dashboard.cards.length}</b><em>추천 문서</em></span>
        <span title="관심 상태에서 추적 중인 토픽 수"><b>{trackedTopics.length || topicCount}</b><em>추적 관심</em></span>
        <span title="현재 활성 상태인 관심 경로 수"><b>{activeTraceCount}</b><em>활성 경로</em></span>
      </div>
    </div>
  );
}

function InterestStructurePanel({
  dashboard,
  interest,
  traces
}: {
  dashboard: DashboardResponse;
  interest: InterestStateResponse | null;
  traces: TraversalTraceSummary[];
}) {
  const layers = buildInterestModelLayers(dashboard, interest, traces);
  return (
    <div className="interestStructure panel" aria-label="관심 구조 시각화">
      {layers.map((layer, index) => (
        <div key={layer.key} className={`modelLayer ${layer.key}`}>
          <div className="modelLayerHead">
            <span>{layer.kicker}</span>
            <b>{layer.title}</b>
          </div>
          <div className="modelNodes">
            {layer.nodes.slice(0, 4).map((node) => (
              <span key={`${layer.key}-${node.rawLabel ?? node.label}`} className={node.tone} title={node.rawLabel ?? node.label}>
                <i>{node.badge ?? compactTopicLabel(node.label)}</i>
                <b>{node.label}</b>
                {node.meta && <em>{node.meta}</em>}
              </span>
            ))}
          </div>
          {index < layers.length - 1 && <i className="modelArrow" aria-hidden="true" />}
        </div>
      ))}
    </div>
  );
}

function SlotBars({ dashboard }: { dashboard: DashboardResponse }) {
  const slots = buildVisibleSlots(dashboard.slots, true, dashboard.cards);
  const total = Math.max(1, slots.reduce((sum, slot) => sum + slot.actual_count, 0));
  return (
    <div className="insightPanel">
      <PanelHeading icon={<BarChart3 size={16} />} title="슬롯 분포" />
      <div className="slotBars">
        {slots.map((slot) => (
          <div key={slot.slot_type} className="barRow">
            <div className="barLabel">
              <span>{slotLabel(slot.slot_type)}</span>
              <b>{slotMeta(slot.actual_count, slot.target_count)}</b>
            </div>
            <div className="barTrack">
              <span className={`barFill ${slot.slot_type}`} style={{ width: `${(slot.actual_count / total) * 100}%` }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function TraceMap({ traces }: { traces: TraversalTraceSummary[] }) {
  const activeTrace = traces.find((trace) => trace.status === "active") ?? traces[0];
  const nodes = activeTrace?.path_labels ?? [];
  return (
    <div className="insightPanel">
      <PanelHeading icon={<GitBranch size={16} />} title="관심 경로" />
      <div className="traceMap" aria-label="interest trace">
        {nodes.length === 0 ? (
          <div className="traceNode"><span>경로 형성 대기</span></div>
        ) : (
          nodes.map((node, index) => (
            <div key={`${node}-${index}`} className={index === nodes.length - 1 ? "traceNode active" : "traceNode"}>
              <span>{node}</span>
            </div>
          ))
        )}
      </div>
      <p className="panelNote">현재 중심 슬롯은 관심 경로 끝단과 하위 리프에서 우선 채웁니다.</p>
    </div>
  );
}

function TopicRanks({ ranks, setView }: { ranks: TopicRank[]; setView: (view: View) => void }) {
  const max = Math.max(1, ...ranks.map((rank) => rank.count));
  return (
    <div className="insightPanel">
      <PanelHeading icon={<TrendingUp size={16} />} title="토픽 순위" />
      <div className="topicRanks">
        {ranks.slice(0, 4).map((rank, index) => (
          <button key={rank.topicId} title={rank.rawLabel ?? rank.label} onClick={() => setView({ name: "topic", topicId: rank.topicId, label: rank.label })}>
            <b>{index + 1}</b>
            <span>{rank.label}</span>
            <i style={{ width: `${(rank.count / max) * 100}%` }} />
          </button>
        ))}
      </div>
    </div>
  );
}

function SourceMix({ rows }: { rows: Array<{ label: string; count: number }> }) {
  const total = Math.max(1, rows.reduce((sum, row) => sum + row.count, 0));
  return (
    <div className="insightPanel compact">
      <PanelHeading icon={<Activity size={16} />} title="출처 구성" />
      <div className="donutRow">
        <div
          className="donut"
          style={{
            background: `conic-gradient(var(--accent) 0 ${percent(rows[0]?.count ?? 0, total)}%, var(--accent-2) 0 ${percent((rows[0]?.count ?? 0) + (rows[1]?.count ?? 0), total)}%, var(--accent-3) 0 100%)`
          }}
        />
        <div className="sourceLegend">
          {rows.map((row) => (
            <span key={row.label}><i /> {row.label} <b>{row.count}</b></span>
          ))}
        </div>
      </div>
    </div>
  );
}

function PanelHeading({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <div className="panelHeading">
      <span>{icon}</span>
      <h3>{title}</h3>
    </div>
  );
}

function DocumentView({
  api,
  documentId,
  setView,
  showToast
}: {
  api: InsightApi;
  documentId: UUID;
  setView: (view: View) => void;
  showToast: (toast: Toast) => void;
}) {
  const [detail, setDetail] = useState<DocumentDetailResponse | null>(null);
  const [summary, setSummary] = useState<DocumentSummaryResponse | null>(null);
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<FeedbackState>({ saved: false, hidden: false, notInterested: false });
  const [busyAction, setBusyAction] = useState<FeedbackAction | null>(null);
  const [traces, setTraces] = useState<TraversalTraceSummary[]>([]);

  useEffect(() => {
    startDwell(documentId);
    void api.documentDetail(documentId).then(setDetail).catch((err) => setError(messageForError(err)));
    void api.documentSummary(documentId).then(setSummary).catch(() => undefined);
    void api.dashboard().then(setDashboard).catch(() => undefined);
    void api.traces().then((res) => setTraces(res.items)).catch(() => undefined);
    return stopDwell;
  }, [api, documentId]);

  useEffect(() => {
    if (!detail) return;
    setFeedback({
      saved: detail.saved,
      hidden: detail.hidden,
      notInterested: detail.not_interested
    });
  }, [detail]);

  const queueCards = useMemo(() => uniqueRecommendationCards(dashboard?.cards ?? []), [dashboard]);

  if (error) return <Empty title="문서를 불러오지 못했습니다" body={error} />;
  if (!detail) return <Loading label="문서를 불러오는 중" />;

  const visibleTopics = detail.related_topics
    .map(displayTopicChip)
    .slice(0, 3);
  const sourceTone = detail.source_type;
  const externalUrl = getExternalUrl(detail.canonical_url ?? detail.url);
  const queueIndex = queueCards.findIndex((card) => card.document_id === documentId);
  const previousCard = queueIndex > 0 ? queueCards[queueIndex - 1] : null;
  const nextCard = queueIndex >= 0 && queueIndex < queueCards.length - 1 ? queueCards[queueIndex + 1] : null;

  function openQueuedDocument(card: RecommendationCard | null) {
    if (!card) return;
    setView({ name: "document", documentId: card.document_id });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function openOriginal() {
    if (!externalUrl) {
      showToast({ tone: "error", text: "열 수 있는 원문 URL이 없습니다." });
      return;
    }

    void api.postEvent({
      event_type: "open_external",
      document_id: documentId,
      occurred_at: new Date().toISOString(),
      client_request_id: crypto.randomUUID()
    }).catch(() => undefined);

    if (window.insightShell) {
      void window.insightShell.openExternal(externalUrl).catch(() => {
        showToast({ tone: "error", text: "원문을 열지 못했습니다." });
      });
      return;
    }

    const opened = window.open(externalUrl, "_blank");
    if (opened) {
      opened.opener = null;
    } else {
      showToast({ tone: "error", text: "팝업 차단을 해제한 뒤 다시 시도해주세요." });
    }
  }

  async function applyFeedback(action: FeedbackAction) {
    const previous = feedback;
    const active =
      action === "save" ? feedback.saved :
      action === "hide" ? feedback.hidden :
      feedback.notInterested;
    setBusyAction(action);
    setFeedback({
      saved: action === "save" ? !feedback.saved : feedback.saved,
      hidden: action === "hide" ? !feedback.hidden : feedback.hidden,
      notInterested: action === "not_interested" ? !feedback.notInterested : feedback.notInterested
    });

    try {
      if (action === "save") {
        if (active) {
          await api.deleteSaved(documentId);
          showToast({ tone: "ok", text: "저장을 해제했습니다." });
        } else {
          await api.saveDocument(documentId);
          showToast({ tone: "ok", text: "저장했습니다." });
        }
      } else if (action === "hide") {
        if (active) {
          await api.deleteHidden(documentId);
          showToast({ tone: "ok", text: "숨김을 해제했습니다." });
        } else {
          await api.hideDocument(documentId);
          showToast({ tone: "ok", text: "숨김 처리했습니다." });
        }
      } else {
        if (active) {
          await api.deleteNotInterested(documentId);
          showToast({ tone: "ok", text: "관심 없음 표시를 해제했습니다." });
        } else {
          await api.notInterestedDocument(documentId);
          showToast({ tone: "ok", text: "관심 없음으로 반영했습니다." });
        }
      }
    } catch (err) {
      setFeedback(previous);
      showToast({ tone: "error", text: messageForError(err) });
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <section>
      <button className="backLink" onClick={() => setView({ name: "dashboard" })}>
        <ArrowLeft size={17} /> 추천으로 돌아가기
      </button>
      <Header title="문서 보기" subtitle={`${sourceTypeLabel(detail.source_type)} · ${formatPublishedDate(detail.published_at, detail.source_name)}`} />
      <div className="documentShell">
        <section className="panel documentHero">
          <div className={`docCover ${sourceTone}`} aria-hidden="true">
            <span>{sourceTypeLabel(detail.source_type)}</span>
            <strong>{sourceInitials(detail.source_name)}</strong>
            <div>
              <i />
              <i />
              <i />
            </div>
          </div>
          <div className="docHeroCopy">
            <div className="docMetaLine">
              <span>{detail.source_name}</span>
              <span>{formatPublishedDate(detail.published_at, detail.source_name)}</span>
            </div>
            <h2>{detail.title}</h2>
            <p>{summary?.reason_short ?? detail.summary_short}</p>
            <div className="docTopicStrip">
              {visibleTopics.map((topic) => (
                <button key={topic.topic_id} title={topic.rawLabel} onClick={() => setView({ name: "topic", topicId: topic.topic_id, label: topic.label })}>{topic.label}</button>
              ))}
            </div>
          </div>
          <div className="docSignalBox" aria-label="문서 신호">
            <span><b>match</b><i><em style={{ width: "88%" }} /></i></span>
            <span><b>trace</b><i><em style={{ width: "72%" }} /></i></span>
            <span><b>fresh</b><i><em style={{ width: "56%" }} /></i></span>
          </div>
        </section>

        <div className="documentWorkspace">
          <article className="documentInsights">
            <section className="docSummaryCard">
              <span className="eyebrow">요약</span>
              <p>{detail.summary_short}</p>
            </section>
            <div className="docSectionGrid">
              {summary?.sections.map((section, index) => (
                <section key={section.section} className={`docSectionCard ${section.section}`}>
                  <b>{String(index + 1).padStart(2, "0")}</b>
                  <h3>{section.title_ko}</h3>
                  <p>{section.body_ko}</p>
                </section>
              ))}
            </div>
          </article>
          <aside className="documentRail">
            <div className="panel documentQueuePanel">
              <div className="documentQueueNav" aria-label="추천 문서 이동">
                <button disabled={!previousCard} title={previousCard?.title ?? "추천 큐의 처음입니다"} onClick={() => openQueuedDocument(previousCard)}>
                  <ArrowLeft size={16} />
                  <span><b>이전 문서</b><small>{previousCard?.title ?? "처음 문서"}</small></span>
                </button>
                <button disabled={!nextCard} title={nextCard?.title ?? "추천 큐의 마지막입니다"} onClick={() => openQueuedDocument(nextCard)}>
                  <ArrowRight size={16} />
                  <span><b>다음 문서</b><small>{nextCard?.title ?? "마지막 문서"}</small></span>
                </button>
              </div>
            </div>
            <div className="panel documentActions">
              <button className="primary" disabled={!externalUrl} onClick={openOriginal}>
                <ExternalLink size={17} /> 원문 열기
              </button>
              <button className={feedback.saved ? "isActive" : ""} aria-pressed={feedback.saved} disabled={busyAction !== null} onClick={() => void applyFeedback("save")}>
                {feedback.saved ? <CheckCircle2 size={16} /> : <Bookmark size={16} />} {feedback.saved ? "저장됨" : "저장"}
              </button>
              <button className={feedback.hidden ? "isActive" : ""} aria-pressed={feedback.hidden} disabled={busyAction !== null} onClick={() => void applyFeedback("hide")}>
                {feedback.hidden ? <CheckCircle2 size={16} /> : <EyeOff size={16} />} {feedback.hidden ? "숨김됨" : "숨김"}
              </button>
              <button className={feedback.notInterested ? "isActive danger" : ""} aria-pressed={feedback.notInterested} disabled={busyAction !== null} onClick={() => void applyFeedback("not_interested")}>
                {feedback.notInterested ? <CheckCircle2 size={16} /> : <HeartCrack size={16} />} {feedback.notInterested ? "관심 없음 반영됨" : "관심 없음"}
              </button>
            </div>
            <div className="panel docTraceCard">
              <PanelHeading icon={<GitBranch size={16} />} title="추천 경로" />
              <div className="docTraceMini">
                {(() => {
                  const relatedLabels = (detail.related_topics ?? []).map((t) => t.label.toLowerCase());
                  const matched =
                    traces.find((t) => t.path_labels.some((p) => relatedLabels.includes(p.toLowerCase()))) ??
                    traces.find((t) => t.status === "active") ??
                    traces[0];
                  const nodes = matched?.path_labels ?? [];
                  if (nodes.length === 0) {
                    return <p className="docTraceEmpty">경로 형성 대기</p>;
                  }
                  return nodes.map((node, index) => (
                    <span key={`${node}-${index}`} className={index === nodes.length - 1 ? "active" : ""} title={node}>
                      <b>{compactTopicLabel(node)}</b>
                      <small>{cleanTopicLabel(node)}</small>
                    </span>
                  ));
                })()}
              </div>
            </div>
          </aside>
        </div>
      </div>
    </section>
  );
}

function TopicView({ api, topicId, label, setView }: { api: InsightApi; topicId: UUID; label: string; setView: (view: View) => void }) {
  const [topic, setTopic] = useState<TopicDocumentsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void api.topicDocuments(topicId).then(setTopic).catch((err) => setError(messageForError(err)));
  }, [api, topicId]);

  return (
    <section>
      <button className="backLink" onClick={() => setView({ name: "dashboard" })}>
        <ArrowLeft size={17} /> 추천으로 돌아가기
      </button>
      <Header title={label} subtitle="관련 최신 문서" />
      {error && <Empty title="토픽 정보를 불러오지 못했습니다" body={error} />}
      {!error && !topic && <Loading label="토픽 문서를 불러오는 중" />}
      {topic && topic.items.length === 0 && <Empty title="관련 최신 문서가 아직 없습니다" body="인접 토픽이나 대시보드 추천을 확인해보세요." />}
      {topic && topic.items.length > 0 && <DocumentList items={topic.items} setView={setView} />}
    </section>
  );
}

function TopicsView({ api, setView }: { api: InsightApi; setView: (view: View) => void }) {
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null);
  const [interest, setInterest] = useState<InterestStateResponse | null>(null);

  useEffect(() => {
    void api.dashboard().then(setDashboard).catch(() => undefined);
    void api.interestState().then(setInterest).catch(() => undefined);
  }, [api]);

  const ranks = useMemo(() => dashboard ? buildTopicRanks(dashboard.cards) : [], [dashboard]);
  const bucketRows = useMemo(() => {
    const topics = (interest?.topics ?? [])
      .map(displayTopicChip);
    if (topics.length) {
      return topics.slice(0, 4).map((topic) => ({
        id: `${topic.cso_topic_id ?? topic.leaf_topic_id}`,
        label: topic.label,
        rawLabel: topic.rawLabel,
        value: bucketLabel(topic.bucket)
      }));
    }
    return ranks.slice(0, 4).map((rank) => ({
      id: rank.topicId,
      label: rank.label,
      rawLabel: rank.rawLabel ?? rank.label,
      value: String(rank.count)
    }));
  }, [interest, ranks]);

  return (
    <section>
      <Header title="토픽 맵" subtitle="현재 관심 경로와 인접 토픽, 추천 근거를 함께 확인합니다." />
      <div className="topicScreen">
        <TraceBoard ranks={ranks} cards={dashboard?.cards ?? []} setView={setView} />
        <aside className="topicSide">
          <div className="insightPanel">
            <PanelHeading icon={<Activity size={16} />} title="Interest buckets" />
            <div className="interestList">
              {bucketRows.map((topic) => (
                <span key={topic.id} title={topic.rawLabel}>
                  {topic.label}<b>{topic.value}</b>
                </span>
              ))}
            </div>
          </div>
          <TopicRanks ranks={ranks} setView={setView} />
        </aside>
      </div>
    </section>
  );
}

function TraceBoard({
  ranks,
  cards,
  setView
}: {
  ranks: TopicRank[];
  cards: RecommendationCard[];
  setView: (view: View) => void;
}) {
  const trace = (ranks.length ? ranks.slice(0, 5) : [
    { topicId: "seed-1", label: "operating systems", count: 2, rawLabel: "operating systems" },
    { topicId: "seed-2", label: "automata theory", count: 2, rawLabel: "automata theory" },
    { topicId: "seed-3", label: "software engineering", count: 1, rawLabel: "software engineering" }
  ]).map((rank, index) => ({
    label: rank.label,
    rawLabel: rank.rawLabel ?? rank.label,
    short: compactTopicLabel(rank.label),
    tone: index === 0 ? "active" : index < 3 ? "core" : "leaf",
    meta: `${rank.count}`,
    weight: Math.min(1, 0.36 + rank.count * 0.16)
  }));
  const adjacent = ranks.slice(0, 6);
  const activeLeaf = trace[0];

  return (
    <div className="traceBoard">
      <div className="traceSummary panel">
        <div className="traceSummaryHead">
          <div>
            <span className="eyebrow">현재 토픽 신호</span>
            <h2 title={activeLeaf.rawLabel}>{activeLeaf.label}</h2>
          </div>
          <div className="leafBadge">
            <span>top topic</span>
            <strong>{activeLeaf.short}</strong>
          </div>
        </div>
        <div className="tracePath topicConstellation">
          {trace.map((node) => (
            <div key={`${node.rawLabel}-${node.label}`} className={`traceStep ${node.tone}`} title={node.rawLabel}>
              <div className="traceStepMain">
                <span>{node.meta}</span>
                <strong>{node.short}</strong>
                <small>{node.label}</small>
              </div>
              <div className="traceWeight">
                <i style={{ width: `${node.weight * 100}%` }} />
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="traceDetailGrid">
        <div className="panel traceEvidence">
          <PanelHeading icon={<BarChart3 size={16} />} title="추천 근거" />
          <div className="evidenceRows">
            {cards.slice(0, 3).map((card, index) => (
              <button key={card.recommendation_id} onClick={() => setView({ name: "document", documentId: card.document_id })}>
                <b>{String(index + 1).padStart(2, "0")}</b>
                <span>
                  <strong>{card.title}</strong>
                  <small>{card.source_name} · {slotLabel(card.slot_type)} · {formatPublishedDate(card.published_at, card.source_name)}</small>
                </span>
                <em title={card.related_topics.map((topic) => topic.label).join(" / ")}>{displayTopicLabels(card.related_topics.map((topic) => topic.label)).slice(0, 2).join(" / ")}</em>
              </button>
            ))}
          </div>
        </div>

        <div className="panel adjacentPanel">
          <PanelHeading icon={<Network size={16} />} title="인접 후보" />
          <div className="adjacentList">
            {adjacent.map((rank) => (
              <button key={rank.topicId} title={rank.rawLabel ?? rank.label} onClick={() => setView({ name: "topic", topicId: rank.topicId, label: rank.label })}>
                <span>{rank.label}</span>
                <b>{rank.count}</b>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function RankingView({ api, setView }: { api: InsightApi; setView: (view: View) => void }) {
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null);

  useEffect(() => {
    void api.dashboard().then(setDashboard).catch(() => undefined);
  }, [api]);

  return (
    <section>
      <Header title="추천 랭킹" subtitle="오늘 추천 큐를 순위, 슬롯, 출처 기준으로 훑어봅니다." />
      {!dashboard ? <Loading label="랭킹을 계산하는 중" /> : (
        <div className="rankScreen">
          <div className="rankTable panel">
            {dashboard.cards.map((card, index) => (
              <button key={card.recommendation_id} onClick={() => setView({ name: "document", documentId: card.document_id })}>
                <b>{String(index + 1).padStart(2, "0")}</b>
                <span className={`slot ${card.slot_type}`}>{slotLabel(card.slot_type)}</span>
                <strong>{card.title}</strong>
                <small>{card.source_name} · {formatPublishedDate(card.published_at, card.source_name)}</small>
              </button>
            ))}
          </div>
          <aside className="insightRail">
            <SlotBars dashboard={dashboard} />
            <SourceMix rows={buildSourceMix(dashboard.cards)} />
          </aside>
        </div>
      )}
    </section>
  );
}

function LibraryView({ api, setView }: { api: InsightApi; setView: (view: View) => void }) {
  const [saved, setSaved] = useState<DocumentSummary[]>([]);
  const [hidden, setHidden] = useState<DocumentSummary[]>([]);
  const [showHidden, setShowHidden] = useState(false);

  useEffect(() => {
    void api.savedDocuments().then((res) => setSaved(res.items)).catch(() => undefined);
    void api.hiddenDocuments().then((res) => setHidden(res.items)).catch(() => undefined);
  }, [api]);

  return (
    <section>
      <Header title="보관함" subtitle="저장한 문서를 중심으로 보고, 숨긴 문서는 필요할 때 복구합니다." />
      <div className="libraryScreen">
        <div className="panel libraryHero">
          <Bookmark size={26} />
          <div>
            <h2>{saved.length}개 저장됨</h2>
            <p>숨긴 문서 {hidden.length}건은 버튼을 눌러 복구할 수 있습니다.</p>
          </div>
          <button className="libraryHiddenTrigger" onClick={() => setShowHidden(true)}>
            <EyeOff size={17} />
            숨긴 문서 {hidden.length}개
          </button>
        </div>
        <div className="libraryColumns">
          <div className="panel librarySavedPanel">
            <h2>저장한 문서</h2>
            {saved.length === 0 ? <p className="muted">저장한 문서가 없습니다. 추천 카드에서 북마크를 눌러보세요.</p> : <DocumentList items={saved} setView={setView} />}
          </div>
          <aside className="panel libraryHiddenPanel">
            <div>
              <h2>숨김 복구</h2>
              <p className="muted">문서를 열어 숨김 상태를 해제할 수 있습니다.</p>
            </div>
            {hidden.length === 0 ? <p className="muted">숨긴 문서가 없습니다.</p> : <DocumentList items={hidden} setView={setView} />}
          </aside>
        </div>
      </div>
      {showHidden && (
        <div className="libraryModalBackdrop" role="presentation" onClick={() => setShowHidden(false)}>
          <section className="libraryModal panel" role="dialog" aria-modal="true" aria-label="숨긴 문서 복구" onClick={(event) => event.stopPropagation()}>
            <div className="libraryModalHead">
              <div>
                <h2>숨긴 문서 복구</h2>
                <p className="muted">문서를 열어 숨김 상태를 해제할 수 있습니다.</p>
              </div>
              <button className="ghostButton" onClick={() => setShowHidden(false)}>닫기</button>
            </div>
            {hidden.length === 0 ? <p className="muted">숨긴 문서가 없습니다.</p> : <DocumentList items={hidden} setView={setView} />}
          </section>
        </div>
      )}
    </section>
  );
}

function SettingsPanel({ api, setView, showToast }: { api: InsightApi; setView: (view: View) => void; showToast: (toast: Toast) => void }) {
  const [interest, setInterest] = useState<InterestStateResponse | null>(null);
  const [saved, setSaved] = useState<DocumentSummary[]>([]);
  const [hidden, setHidden] = useState<DocumentSummary[]>([]);

  useEffect(() => {
    void api.interestState().then(setInterest).catch(() => undefined);
    void api.savedDocuments().then((data) => setSaved(data.items)).catch(() => undefined);
    void api.hiddenDocuments().then((data) => setHidden(data.items)).catch(() => undefined);
  }, [api]);

  async function logout() {
    const refreshToken = await tokenStore.getRefreshToken();
    await api.logout(refreshToken).catch(() => undefined);
    await tokenStore.clearTokens();
    setView({ name: "auth" });
  }

  const activeTopics = (interest?.topics ?? []).filter((topic) => topic.bucket !== "neutral");
  const previewTopics = activeTopics.slice(0, 4);
  const updatedAt = interest?.updated_at ? new Date(interest.updated_at).toLocaleDateString("ko-KR") : "아직 없음";

  return (
    <section>
      <Header title="설정" subtitle="추천 상태와 계정 관리를 확인합니다." />
      <div className="settingsGrid">
        <div className="panel settingsCard">
          <PanelHeading icon={<Activity size={16} />} title="추천 상태" />
          <div className="settingsStats">
            <span>
              <b>{activeTopics.length}</b>
              활성 관심사
            </span>
            <span>
              <b>{interest?.topics.length ?? 0}</b>
              전체 버킷
            </span>
            <span>
              <b>{updatedAt}</b>
              갱신일
            </span>
          </div>
          {previewTopics.length === 0 ? (
            <p className="muted">아직 추적 중인 관심사가 없습니다.</p>
          ) : (
            <div className="settingsPreview">
              {previewTopics.map((topic) => (
                <span key={`${topic.cso_topic_id ?? topic.leaf_topic_id ?? topic.label}`}>
                  {topic.label}
                  <b>{bucketLabel(topic.bucket)}</b>
                </span>
              ))}
            </div>
          )}
          <button className="ghostButton" onClick={() => setView({ name: "topics" })}>
            <Network size={16} /> 토픽 맵에서 보기
          </button>
        </div>

        <div className="panel settingsCard">
          <PanelHeading icon={<Library size={16} />} title="보관함 요약" />
          <p className="muted">저장과 숨김 목록은 보관함 화면에서 한 번에 관리합니다.</p>
          <div className="settingsStats">
            <span>
              <b>{saved.length}</b>
              저장
            </span>
            <span>
              <b>{hidden.length}</b>
              숨김
            </span>
            <span>
              <b>{saved.length + hidden.length}</b>
              전체
            </span>
          </div>
          <button className="ghostButton" onClick={() => setView({ name: "library" })}>
            <Bookmark size={16} /> 보관함 열기
          </button>
        </div>

        <div className="panel sidePanel settingsCard">
          <PanelHeading icon={<Settings size={16} />} title="계정 관리" />
          <p className="muted">동의 철회는 추천 흐름을 멈추고 온보딩으로 돌아갑니다.</p>
          <button
            onClick={() =>
              void api.revokeConsent().then(() => {
                showToast({ tone: "ok", text: "동의가 철회되었습니다." });
                setView({ name: "onboarding", me: null });
              })
            }
          >
            <Trash2 size={16} /> 동의 철회
          </button>
          <button onClick={() => void logout()}>
            <LogOut size={16} /> 로그아웃
          </button>
        </div>
      </div>
    </section>
  );
}

function Header({ title, subtitle, children }: { title: string; subtitle: string; children?: React.ReactNode }) {
  return (
    <header className="header">
      <div>
        <h1>{title}</h1>
        <p>{subtitle}</p>
      </div>
      <div className="headerActions">{children}</div>
    </header>
  );
}

function Loading({ label }: { label: string }) {
  return <div className="loading"><Loader2 className="spin" size={22} /> {label}</div>;
}

function Empty({ title, body }: { title: string; body: string }) {
  return <div className="empty"><h2>{title}</h2><p>{body}</p></div>;
}

function DocumentList({ items, setView }: { items: DocumentSummary[]; setView: (view: View) => void }) {
  return (
    <div className="docList">
      {items.map((item) => (
        <button key={item.document_id} onClick={() => setView({ name: "document", documentId: item.document_id })}>
          <strong>{item.title}</strong>
          <span>{item.source_name} · {formatPublishedDate(item.published_at, item.source_name)}</span>
        </button>
      ))}
    </div>
  );
}

async function pollColdStart(api: InsightApi, requestId: UUID): Promise<void> {
  const startedAt = Date.now();
  while (Date.now() - startedAt < 60_000) {
    const status = await api.coldStartStatus(requestId);
    if (status.status === "completed" && status.dashboard_ready) return;
    if (status.status === "failed") return;
    await new Promise((resolve) => window.setTimeout(resolve, window.__INSIGHT_DEMO_MODE__ ? 500 : 1000));
  }
}

function toggleSet<T>(set: Set<T>, setter: (next: Set<T>) => void, value: T) {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  setter(next);
}

function messageForError(err: unknown): string {
  const maybe = err as Partial<ApiError>;
  if (maybe.status === 429) {
    return "요청이 너무 많습니다. 잠시 후 다시 시도해주세요.";
  }
  if (maybe.code === "auth.weak_password") {
    const subCode = (maybe.details?.sub_code as string | undefined) ?? "";
    return passwordPolicyMessage(subCode);
  }
  if (maybe.code === "auth.email_taken") {
    return "이미 가입된 이메일입니다.";
  }
  if (maybe.code === "auth.invalid_credentials") {
    return "이메일 또는 비밀번호가 올바르지 않습니다.";
  }
  if (maybe.code === "validation_error" || maybe.status === 422) {
    return "입력값을 확인해주세요. 이메일 형식과 비밀번호 규칙을 맞춰야 합니다.";
  }
  return maybe.message || "처리 중 문제가 발생했습니다.";
}

function validateLoginForm(email: string, password: string): string | null {
  if (!isValidEmail(email)) {
    return "올바른 이메일 주소를 입력해주세요.";
  }
  if (!password) {
    return "비밀번호를 입력해주세요.";
  }
  return null;
}

function validateSignupForm(email: string, password: string): string | null {
  const loginError = validateLoginForm(email, password);
  if (loginError) return loginError;
  if (password.trim() !== password) {
    return passwordPolicyMessage("whitespace");
  }
  if (password.length < 12) {
    return passwordPolicyMessage("too_short");
  }
  if (password.length > 128) {
    return passwordPolicyMessage("too_long");
  }
  const lowered = password.toLowerCase();
  const localPart = email.split("@", 1)[0]?.toLowerCase() ?? "";
  if (localPart.length >= 4 && lowered.includes(localPart)) {
    return passwordPolicyMessage("contains_user_info");
  }
  if (["insight", "skku", "admin", "password", "qwerty"].some((term) => lowered.includes(term))) {
    return passwordPolicyMessage("forbidden_term");
  }
  return null;
}

function isValidEmail(email: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

function passwordPolicyMessage(subCode: string): string {
  return {
    whitespace: "비밀번호 앞뒤에는 공백을 넣을 수 없습니다.",
    too_short: "비밀번호는 12자 이상이어야 합니다.",
    too_long: "비밀번호는 128자 이하여야 합니다.",
    common: "너무 흔한 비밀번호입니다. 다른 비밀번호를 사용해주세요.",
    contains_user_info: "비밀번호에 이메일 아이디를 포함할 수 없습니다.",
    forbidden_term: "비밀번호에 insight, skku, admin, password, qwerty 같은 금칙어를 포함할 수 없습니다."
  }[subCode] ?? "비밀번호 정책을 확인해주세요. 12자 이상, 금칙어와 이메일 아이디 제외, 앞뒤 공백 없음.";
}

function slotLabel(slot: string): string {
  return {
    core: "중심",
    adjacent: "인접",
    discovery: "탐색",
    fallback_adjacent: "대체",
    fallback_trend: "트렌드"
  }[slot] ?? slot;
}

function slotBadge(slot: string): string {
  return {
    core: "중",
    adjacent: "인",
    discovery: "탐",
    fallback_adjacent: "대",
    fallback_trend: "T"
  }[slot] ?? compactTopicLabel(slot);
}

function slotMeta(actualCount: number, _targetCount: number): string {
  return `${actualCount}`;
}

function buildVisibleSlots(
  slots: SlotSummaryItem[],
  includeEmptyBase: boolean,
  cards: RecommendationCard[] = []
): SlotSummaryItem[] {
  const byType = new Map(slots.map((slot) => [slot.slot_type, slot]));
  const cardCounts = cards.reduce((counts, card) => {
    counts.set(card.slot_type, (counts.get(card.slot_type) ?? 0) + 1);
    return counts;
  }, new Map<SlotSummaryItem["slot_type"], number>());
  const baseSlots = Object.entries(baseSlotTargets)
    .map(([slotType, targetCount]) => {
      const typedSlot = slotType as SlotSummaryItem["slot_type"];
      const actualCount = cardCounts.get(typedSlot) ?? byType.get(typedSlot)?.actual_count ?? 0;
      return byType.get(typedSlot) ?? {
        slot_type: typedSlot,
        actual_count: actualCount,
        target_count: targetCount,
        fallback_reason: null
      };
    })
    .map((slot) => ({ ...slot, actual_count: cardCounts.get(slot.slot_type) ?? slot.actual_count }))
    .filter((slot) => includeEmptyBase || slot.actual_count > 0);
  const fallbackSlots = slotOrder
    .filter((slotType) => slotType.startsWith("fallback_"))
    .map((slotType) => {
      const typedSlot = slotType as SlotSummaryItem["slot_type"];
      const existing = byType.get(typedSlot);
      return {
        slot_type: typedSlot,
        target_count: existing?.target_count ?? 0,
        actual_count: cardCounts.get(typedSlot) ?? existing?.actual_count ?? 0,
        fallback_reason: existing?.fallback_reason ?? null
      };
    })
    .filter((slot) => slot.actual_count > 0);
  return [...baseSlots, ...fallbackSlots]
    .sort((a, b) => slotOrder.indexOf(a.slot_type) - slotOrder.indexOf(b.slot_type));
}

function sourceTypeLabel(sourceType: string): string {
  return {
    academic: "논문",
    vendor_blog: "블로그",
    tech_news: "뉴스"
  }[sourceType] ?? sourceType;
}

function sourceInitials(sourceName: string): string {
  return sourceName
    .split(/\s+/)
    .map((part) => part[0])
    .join("")
    .slice(0, 3)
    .toUpperCase();
}

function buildTrackedNodes(
  dashboard: DashboardResponse,
  interest: InterestStateResponse | null,
  traces: TraversalTraceSummary[]
): string[] {
  return buildTrackedNodeItems(dashboard, interest, traces).map((node) => node.label);
}

function buildTrackedNodeItems(
  dashboard: DashboardResponse,
  interest: InterestStateResponse | null,
  traces: TraversalTraceSummary[]
): DisplayLabel[] {
  const labels: string[] = [];
  for (const trace of traces.filter((item) => item.status === "active")) {
    labels.push(...trace.path_labels);
  }
  labels.push(
    ...(interest?.topics ?? [])
      .filter((topic) => topic.bucket !== "neutral")
      .map((topic) => topic.label)
  );
  labels.push(
    ...dashboard.cards.flatMap((card) =>
      card.related_topics.map((topic) => topic.label)
    )
  );

  return displayTopicNodes(labels).slice(0, 5);
}

function signalMapPoint(index: number, total: number): { x: number; y: number } {
  if (total <= 1) return { x: 50, y: 54 };
  const yPattern = [64, 32, 56, 34, 62];
  return {
    x: 12 + (76 * index) / (total - 1),
    y: yPattern[index] ?? 50
  };
}

function buildInterestModelLayers(
  dashboard: DashboardResponse,
  interest: InterestStateResponse | null,
  traces: TraversalTraceSummary[]
): ModelLayer[] {
  const interestTopics = (interest?.topics ?? [])
    .map(displayTopicChip);
  const trackedTopics = interestTopics.filter((topic) => topic.bucket !== "neutral");
  const fallbackTopics = displayTopicNodes(
    dashboard.cards.flatMap((card) => card.related_topics.map((topic) => topic.label))
  );
  const activeTraces = traces.filter((trace) => trace.status === "active");

  return [
    {
      key: "prior",
      kicker: "1",
      title: "초기 seed",
      nodes: (trackedTopics.length ? trackedTopics : fallbackTopics)
        .slice(0, 3)
        .map((topic) => ({ label: topic.label, rawLabel: topic.rawLabel, tone: "prior", meta: "CSO seed" }))
    },
    {
      key: "bayes",
      kicker: "2",
      title: "Bayesian 신호",
      nodes: (trackedTopics.length ? trackedTopics : interestTopics.slice(0, 4))
        .slice(0, 4)
        .map((topic) => ({ label: topic.label, rawLabel: topic.rawLabel, tone: topic.bucket, meta: bucketLabel(topic.bucket) }))
    },
    {
      key: "trace",
      kicker: "3",
      title: "활성 trace",
      nodes: activeTraces.length
        ? activeTraces.slice(0, 4).map((trace) => ({
            label: displayTopicLabels(trace.path_labels).join(" > "),
            rawLabel: trace.path_labels.join(" > "),
            tone: "trace",
            meta: trace.path_labels.length > 1 ? `${trace.path_labels.length} nodes` : "단일 노드"
          })).filter((node) => node.label.length > 0)
        : buildTrackedNodes(dashboard, interest, traces)
            .slice(0, 4)
            .map((label) => ({ label, rawLabel: label, tone: "trace", meta: "생성 대기" }))
    },
    {
      key: "slots",
      kicker: "4",
      title: "추천 슬롯",
      nodes: buildVisibleSlots(dashboard.slots, true, dashboard.cards)
        .map((slot) => ({
          label: slotLabel(slot.slot_type),
          tone: slot.slot_type,
          badge: slotBadge(slot.slot_type),
          meta: slotMeta(slot.actual_count, slot.target_count)
        }))
    }
  ].map((layer) => ({
    ...layer,
    nodes: layer.nodes.length ? layer.nodes : [{ label: "대기 중", tone: "neutral", meta: "none" }]
  }));
}

function uniqueLabels(labels: string[]): string[] {
  return labels
    .map((label) => label.trim())
    .filter(Boolean)
    .filter((label, index, arr) => arr.findIndex((other) => other.toLowerCase() === label.toLowerCase()) === index);
}

function cleanTopicLabel(label: string): string {
  const stripped = label
    .trim()
    .replace(/^[^\p{L}\p{N}(+]+/u, "")
    .trim();
  const alphaCount = (stripped.match(/\p{L}/gu) ?? []).length;
  if (alphaCount < 2 && !/[0-9+]/.test(stripped)) return "수식 토픽";
  return stripped;
}

function displayTopicLabels(labels: string[]): string[] {
  return displayTopicNodes(labels).map((node) => node.label);
}

function displayTopicNodes(labels: string[]): DisplayLabel[] {
  const seen = new Set<string>();
  const out: DisplayLabel[] = [];
  for (const rawLabel of labels) {
    const label = cleanTopicLabel(rawLabel);
    const key = label.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ label, rawLabel });
  }
  return out;
}

function displayTopicChip<T extends { label: string }>(topic: T): DisplayTopic<T> {
  return { ...topic, label: cleanTopicLabel(topic.label), rawLabel: topic.label };
}

function compactTopicLabel(label: string): string {
  const cleanLabel = cleanTopicLabel(label) ?? label;
  const graphLabel = cleanLabel.replace(/^\(([^)]{1,40})\)\s*/, "$1 ").trim();
  const words = graphLabel.split(/[\s/.,()+-]+/).filter((word) => /\p{L}|\p{N}/u.test(word));
  if (words.length === 0) return cleanLabel.slice(0, 3).toUpperCase();
  if (words.length === 1) return words[0].slice(0, 3).toUpperCase();
  return words.slice(0, 2).map((word) => word[0]).join("").toUpperCase();
}

function uniqueRecommendationCards(cards: RecommendationCard[]): RecommendationCard[] {
  const seen = new Set<UUID>();
  return cards.filter((card) => {
    if (seen.has(card.document_id)) return false;
    seen.add(card.document_id);
    return true;
  });
}

function buildTopicRanks(cards: RecommendationCard[]): TopicRank[] {
  const map = new Map<UUID, TopicRank>();
  for (const card of cards) {
    for (const topic of card.related_topics) {
      const label = cleanTopicLabel(topic.label);
      if (!label) continue;
      const current = map.get(topic.topic_id);
      map.set(topic.topic_id, {
      topicId: topic.topic_id,
      label,
      rawLabel: topic.label,
      count: (current?.count ?? 0) + 1
    });
  }
  }
  return [...map.values()].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

function buildSourceMix(cards: RecommendationCard[]): Array<{ label: string; count: number }> {
  const labels: Record<string, string> = {
    academic: "논문",
    vendor_blog: "블로그",
    tech_news: "뉴스"
  };
  const map = new Map<string, number>();
  for (const card of cards) {
    const label = labels[card.source_type] ?? card.source_type;
    map.set(label, (map.get(label) ?? 0) + 1);
  }
  return [...map.entries()].map(([label, count]) => ({ label, count }));
}

function percent(value: number, total: number): number {
  return Math.round((value / total) * 100);
}

function bucketLabel(bucket: string): string {
  return { high: "높음", medium: "보통", low: "낮음", neutral: "중립" }[bucket] ?? bucket;
}

function getExternalUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", { month: "short", day: "numeric" }).format(new Date(value));
}

function formatPublishedDate(value: string, sourceName: string): string {
  const date = new Date(value);
  if (sourceName === "cold_start_pseudo" && date.getUTCMonth() === 0 && date.getUTCDate() === 1) {
    return `${date.getUTCFullYear()}년`;
  }
  return formatDate(value);
}
