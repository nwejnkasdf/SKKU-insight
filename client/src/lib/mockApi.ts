import type {
  ColdStartStatusResponse,
  ConsentStateResponse,
  CSOCluster,
  DashboardResponse,
  DocumentDetailResponse,
  DocumentSummary,
  DocumentSummaryResponse,
  EventResponse,
  InsightApi,
  InterestStateResponse,
  MeResponse,
  OnboardingInterestsResponse,
  PagedResponse,
  SignupResponse,
  TokenPair,
  TopicDocumentsResponse,
  UUID
} from "../generated/api";

type MockUser = {
  userId: UUID;
  email: string;
  consentActive: boolean;
  onboardingComplete: boolean;
};

const now = () => new Date().toISOString();

const clusters: CSOCluster[] = [
  ["Artificial Intelligence", "머신러닝, 자연어처리, 비전, 로보틱스"],
  ["Computer Systems", "운영체제, 분산 시스템, 클라우드 인프라"],
  ["Cybersecurity", "보안, 개인정보보호, 인증과 위협 모델링"],
  ["Software Engineering", "테스트, 아키텍처, 개발자 도구와 품질"],
  ["Human-Computer Interaction", "사용자 경험, 접근성, 인터랙션 디자인"],
  ["Information Retrieval", "검색, 추천, 랭킹, 질의 이해"],
  ["Databases", "쿼리 시스템, 저장소, 트랜잭션, 인덱싱"],
  ["Networks", "프로토콜, 엣지, 무선 네트워크"],
  ["Theory", "알고리즘, 복잡도, 오토마타 이론"],
  ["Graphics & Multimedia", "렌더링, 미디어 시스템, 시각화"],
  ["Hardware", "컴퓨터 구조, 가속기, 임베디드 시스템"],
  ["Computational Science", "시뮬레이션, 과학 계산, 모델링"]
].map(([label, description], index) => ({
  cso_topic_id: mockId(`cluster-${index + 1}`),
  label,
  description_ko: description,
  document_count: 12 + index * 3
}));

const topics = clusters.slice(0, 8).map((cluster) => ({
  topic_id: cluster.cso_topic_id,
  label: cluster.label,
  type: "cso" as const
}));

const documents: DocumentDetailResponse[] = [
  {
    document_id: mockId("doc-1"),
    title: "Agentic Retrieval Pipelines for Technical Literature",
    source_name: "arXiv",
    source_type: "academic",
    url: "https://arxiv.org/",
    canonical_url: "https://arxiv.org/mock-agentic-retrieval",
    published_at: daysAgo(1),
    summary_short:
      "사용자 관심 trace를 검색 쿼리 생성의 중심에 두고, 검색 결과를 문서 단위 추천 후보로 정리하는 방법을 다룹니다.",
    related_topics: [topics[0], topics[5]],
    saved: false,
    hidden: false,
    not_interested: false
  },
  {
    document_id: mockId("doc-2"),
    title: "Production Notes on Long-Context Evaluation",
    source_name: "OpenAI Blog",
    source_type: "vendor_blog",
    url: "https://openai.com/",
    canonical_url: "https://openai.com/mock-long-context",
    published_at: daysAgo(2),
    summary_short:
      "긴 컨텍스트 모델을 실제 제품에 넣을 때 평가 세트, 실패 분석, 사용자 피드백 루프를 어떻게 나눌지 설명합니다.",
    related_topics: [topics[0], topics[3]],
    saved: false,
    hidden: false,
    not_interested: false
  },
  {
    document_id: mockId("doc-3"),
    title: "Cache-Aware Scheduling for Small Team AI Services",
    source_name: "Engineering Blog",
    source_type: "vendor_blog",
    url: "https://example.com/cache-aware-scheduling",
    canonical_url: null,
    published_at: daysAgo(3),
    summary_short:
      "작은 데모 환경에서 Redis lock, batch flush, worker scheduling으로 응답성과 비용을 같이 지키는 패턴을 정리합니다.",
    related_topics: [topics[1], topics[6]],
    saved: false,
    hidden: false,
    not_interested: false
  },
  {
    document_id: mockId("doc-4"),
    title: "Bayesian Interest Modeling without Embeddings",
    source_name: "Research Digest",
    source_type: "academic",
    url: "https://example.com/bayesian-interest",
    canonical_url: null,
    published_at: daysAgo(5),
    summary_short:
      "사용자 이벤트를 Beta-Bernoulli 업데이트로 반영하고, 점수 대신 bucket만 노출하는 개인화 모델을 소개합니다.",
    related_topics: [topics[5], topics[8 % topics.length]],
    saved: false,
    hidden: false,
    not_interested: false
  },
  {
    document_id: mockId("doc-5"),
    title: "Practical Threat Modeling for Desktop Research Tools",
    source_name: "Security Notes",
    source_type: "tech_news",
    url: "https://example.com/desktop-threat-modeling",
    canonical_url: null,
    published_at: daysAgo(6),
    summary_short:
      "Electron 앱에서 토큰 저장, 외부 링크 열기, 사용자 행동 로그 처리 시 고려할 보안 경계를 설명합니다.",
    related_topics: [topics[2], topics[4]],
    saved: false,
    hidden: false,
    not_interested: false
  }
];

const cards = documents.concat(documents).slice(0, 10).map((document, index) => ({
  recommendation_id: mockId(`rec-${index + 1}`),
  document_id: document.document_id,
  slot_type: index < 5 ? "core" as const : index < 8 ? "adjacent" as const : "discovery" as const,
  title: document.title,
  source_name: document.source_name,
  source_type: document.source_type,
  related_topics: document.related_topics,
  reason_short:
    index < 5
      ? "최근 선택한 관심 클러스터와 직접 맞닿아 있습니다."
      : index < 8
        ? "현재 관심과 한 단계 인접한 토픽입니다."
        : "새로운 방향을 가볍게 탐색하기 좋은 문서입니다.",
  published_at: document.published_at,
  thumbnail_url: null,
  saved: false,
  hidden: false,
  not_interested: false
}));

let user: MockUser | null = null;
let saved = new Set<UUID>();
let hidden = new Set<UUID>();
let notInterested = new Set<UUID>();
let coldStartReady = false;

export class MockInsightApi implements Partial<InsightApi> {
  async signup(email: string): Promise<SignupResponse> {
    user = {
      userId: mockId("user-demo"),
      email,
      consentActive: false,
      onboardingComplete: false
    };
    return {
      user_id: user.userId,
      email,
      onboarding_required: true,
      consent_required: true
    };
  }

  async login(email: string): Promise<TokenPair> {
    user ??= {
      userId: mockId("user-demo"),
      email,
      consentActive: false,
      onboardingComplete: false
    };
    return {
      access_token: "mock-access-token",
      refresh_token: "mock-refresh-token",
      token_type: "Bearer",
      expires_in: 900
    };
  }

  async logout(): Promise<void> {
    user = null;
    saved = new Set();
    hidden = new Set();
    notInterested = new Set();
    coldStartReady = false;
  }

  async me(): Promise<MeResponse> {
    if (!user) {
      throw new Error("mock user is not logged in");
    }
    return {
      user_id: user.userId,
      email: user.email,
      created_at: daysAgo(12),
      consent_active: user.consentActive,
      onboarding_complete: user.onboardingComplete
    };
  }

  async consentState(): Promise<ConsentStateResponse> {
    return this.consentResponse();
  }

  async consentAgree(): Promise<ConsentStateResponse> {
    this.ensureUser().consentActive = true;
    return this.consentResponse();
  }

  async revokeConsent(): Promise<ConsentStateResponse> {
    this.ensureUser().consentActive = false;
    this.ensureUser().onboardingComplete = false;
    return this.consentResponse();
  }

  async clusters() {
    await delay(160);
    return { clusters };
  }

  async submitInterests(): Promise<OnboardingInterestsResponse> {
    const current = this.ensureUser();
    current.onboardingComplete = true;
    coldStartReady = false;
    window.setTimeout(() => {
      coldStartReady = true;
    }, 700);
    return {
      request_id: mockId("cold-start"),
      status: "queued",
      polling_url: "/onboarding/cold-start-status/mock",
      estimated_seconds: 1
    };
  }

  async coldStartStatus(requestId: UUID): Promise<ColdStartStatusResponse> {
    await delay(250);
    return {
      request_id: requestId,
      status: coldStartReady ? "completed" : "running",
      progress_percent: coldStartReady ? 100 : 65,
      completed_at: coldStartReady ? now() : null,
      dashboard_ready: coldStartReady,
      error_code: null
    };
  }

  async dashboard(): Promise<DashboardResponse> {
    await delay(180);
    return dashboardResponse("hit");
  }

  async refreshDashboard(): Promise<DashboardResponse> {
    await delay(260);
    return dashboardResponse("miss");
  }

  async documentDetail(documentId: UUID): Promise<DocumentDetailResponse> {
    const document = findDocument(documentId);
    return {
      ...document,
      saved: saved.has(documentId),
      hidden: hidden.has(documentId),
      not_interested: notInterested.has(documentId)
    };
  }

  async documentSummary(documentId: UUID): Promise<DocumentSummaryResponse> {
    const document = findDocument(documentId);
    return {
      document_id: documentId,
      generator: "llm",
      generated_at: now(),
      reason_short: "현재 관심 trace에서 자주 등장한 토픽과 연결되어 있습니다.",
      sections: [
        {
          section: "core",
          title_ko: "핵심",
          body_ko: document.summary_short
        },
        {
          section: "background",
          title_ko: "배경",
          body_ko: "사용자별 CSO trace와 동적 리프 토픽을 기준으로 문서를 먼저 좁히는 흐름과 잘 맞습니다."
        },
        {
          section: "significance",
          title_ko: "의의",
          body_ko: "시연에서는 추천 이유와 저장, 숨김, 관심 없음 액션의 반응성을 보여주기에 적합합니다."
        },
        {
          section: "limitations",
          title_ko: "한계",
          body_ko: "mock 데이터이므로 실제 출처 품질과 최신성은 백엔드 수집 모듈 연결 이후 검증해야 합니다."
        }
      ]
    };
  }

  async topicDocuments(topicId: UUID): Promise<TopicDocumentsResponse> {
    const items = documents
      .filter((document) => document.related_topics.some((topic) => topic.topic_id === topicId))
      .map(toDocumentSummary);
    return {
      topic_type: "cso",
      topic_id: topicId,
      items,
      meta: { next_cursor: null, has_more: false, page_size: items.length }
    };
  }

  async interestState(): Promise<InterestStateResponse> {
    return {
      user_id: this.ensureUser().userId,
      updated_at: now(),
      topics: clusters.slice(0, 6).map((cluster, index) => ({
        cso_topic_id: cluster.cso_topic_id,
        leaf_topic_id: null,
        label: cluster.label,
        bucket: index < 2 ? "high" : index < 4 ? "medium" : "neutral",
        is_onboarding_selected: index < 4
      }))
    };
  }

  async postEvent(): Promise<EventResponse> {
    return eventResponse();
  }

  async saveDocument(documentId: UUID): Promise<EventResponse> {
    saved.add(documentId);
    notInterested.delete(documentId);
    return eventResponse();
  }

  async hideDocument(documentId: UUID): Promise<EventResponse> {
    hidden.add(documentId);
    return eventResponse();
  }

  async notInterestedDocument(documentId: UUID): Promise<EventResponse> {
    notInterested.add(documentId);
    hidden.add(documentId);
    return eventResponse();
  }

  async savedDocuments(): Promise<PagedResponse<DocumentSummary>> {
    const items = documents.filter((document) => saved.has(document.document_id)).map(toDocumentSummary);
    return paged(items);
  }

  async hiddenDocuments(): Promise<PagedResponse<DocumentSummary>> {
    const items = documents.filter((document) => hidden.has(document.document_id)).map(toDocumentSummary);
    return paged(items);
  }

  async deleteSaved(documentId: UUID): Promise<void> {
    saved.delete(documentId);
  }

  async deleteHidden(documentId: UUID): Promise<void> {
    hidden.delete(documentId);
  }

  async deleteNotInterested(documentId: UUID): Promise<void> {
    notInterested.delete(documentId);
    hidden.delete(documentId);
  }

  private ensureUser(): MockUser {
    user ??= {
      userId: mockId("user-demo"),
      email: "test@skku.edu",
      consentActive: false,
      onboardingComplete: false
    };
    return user;
  }

  private consentResponse(): ConsentStateResponse {
    const current = this.ensureUser();
    return {
      user_id: current.userId,
      active: current.consentActive,
      onboarding_required: !current.onboardingComplete,
      records: current.consentActive
        ? [
            {
              consent_id: mockId("consent"),
              consent_type: "personalization",
              agreed_at: now(),
              revoked_at: null
            }
          ]
        : []
    };
  }
}

function dashboardResponse(cache: "hit" | "miss"): DashboardResponse {
  return {
    user_id: user?.userId ?? mockId("user-demo"),
    cards: cards
      .filter((card) => !hidden.has(card.document_id))
      .map((card) => ({
        ...card,
        saved: saved.has(card.document_id),
        hidden: hidden.has(card.document_id),
        not_interested: notInterested.has(card.document_id)
      })),
    slots: [
      { slot_type: "core", target_count: 5, actual_count: 5, fallback_reason: null },
      { slot_type: "adjacent", target_count: 3, actual_count: 3, fallback_reason: null },
      { slot_type: "discovery", target_count: 2, actual_count: 2, fallback_reason: null }
    ],
    generated_at: now(),
    cache,
    cold_start: true,
    collection_in_progress: false
  };
}

function eventResponse(): EventResponse {
  return {
    event_id: crypto.randomUUID(),
    accepted: true,
    server_received_at: now(),
    error_code: null
  };
}

function findDocument(documentId: UUID): DocumentDetailResponse {
  const document = documents.find((item) => item.document_id === documentId);
  if (!document) {
    throw new Error("문서를 찾을 수 없습니다.");
  }
  return document;
}

function toDocumentSummary(document: DocumentDetailResponse): DocumentSummary {
  return {
    document_id: document.document_id,
    title: document.title,
    source_name: document.source_name,
    source_type: document.source_type,
    published_at: document.published_at,
    url: document.url,
    related_topics: document.related_topics
  };
}

function paged<T>(items: T[]): PagedResponse<T> {
  return {
    items,
    meta: { next_cursor: null, has_more: false, page_size: items.length }
  };
}

function mockId(seed: string): UUID {
  let hash = 0;
  for (let i = 0; i < seed.length; i += 1) {
    hash = (hash * 31 + seed.charCodeAt(i)) >>> 0;
  }
  return `00000000-0000-4000-8000-${hash.toString(16).padStart(12, "0").slice(0, 12)}`;
}

function daysAgo(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date.toISOString();
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}
