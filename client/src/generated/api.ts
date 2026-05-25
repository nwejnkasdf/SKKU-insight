export type UUID = string;

export type SourceType = "academic" | "vendor_blog" | "tech_news";
export type SlotType =
  | "core"
  | "adjacent"
  | "discovery"
  | "fallback_adjacent"
  | "fallback_trend";
export type EventType =
  | "view"
  | "click"
  | "dwell_tick"
  | "open_external"
  | "save"
  | "hide"
  | "not_interested";
export type InterestBucket = "high" | "medium" | "low" | "neutral";

export type ErrorResponse = {
  code: string;
  message: string;
  details: Record<string, unknown> | null;
  request_id?: string | null;
};

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown> | null;

  constructor(status: number, error: ErrorResponse) {
    super(error.message || error.code);
    this.name = "ApiError";
    this.status = status;
    this.code = error.code;
    this.details = error.details;
  }
}

export type TokenPair = {
  access_token: string;
  refresh_token: string;
  token_type: "Bearer";
  expires_in: number;
};

export type SignupResponse = {
  user_id: UUID;
  email: string;
  onboarding_required: boolean;
  consent_required: boolean;
};

export type MeResponse = {
  user_id: UUID;
  email: string;
  created_at: string;
  consent_active: boolean;
  onboarding_complete: boolean;
};

export type ConsentStateResponse = {
  user_id: UUID;
  records: Array<{
    consent_id: UUID;
    consent_type: "personalization";
    agreed_at: string;
    revoked_at: string | null;
  }>;
  active: boolean;
  onboarding_required: boolean;
};

export type CSOCluster = {
  cso_topic_id: UUID;
  label: string;
  description_ko: string;
  document_count: number;
};

export type ClustersResponse = {
  clusters: CSOCluster[];
};

export type OnboardingInterestsResponse = {
  request_id: UUID;
  status: "queued" | "completed";
  polling_url: string;
  estimated_seconds: number;
};

export type ColdStartStatusResponse = {
  request_id: UUID;
  status: "queued" | "running" | "completed" | "failed";
  progress_percent: number;
  completed_at: string | null;
  dashboard_ready: boolean;
  error_code: string | null;
};

export type TopicChip = {
  topic_id: UUID;
  label: string;
  type: "cso" | "leaf";
};

export type RecommendationCard = {
  recommendation_id: UUID;
  document_id: UUID;
  slot_type: SlotType;
  title: string;
  source_name: string;
  source_type: SourceType;
  related_topics: TopicChip[];
  reason_short: string;
  published_at: string;
  thumbnail_url: string | null;
  saved: boolean;
  hidden: boolean;
  not_interested: boolean;
};

export type SlotSummary = {
  slot_type: SlotType;
  target_count: number;
  actual_count: number;
  fallback_reason: string | null;
};

export type DashboardResponse = {
  user_id: UUID;
  cards: RecommendationCard[];
  slots: SlotSummary[];
  generated_at: string;
  cache: "hit" | "miss";
  cold_start: boolean;
  // C-61 후속 — true 시 refresh 버튼 비활성 + 폴링. 백엔드 refresh endpoint 도 409 차단.
  collection_in_progress: boolean;
};

export type DocumentSummary = {
  document_id: UUID;
  title: string;
  source_name: string;
  source_type: SourceType;
  published_at: string;
  url: string;
  related_topics: TopicChip[];
};

export type DocumentDetailResponse = {
  document_id: UUID;
  title: string;
  source_name: string;
  source_type: SourceType;
  url: string;
  canonical_url: string | null;
  published_at: string;
  summary_short: string;
  related_topics: TopicChip[];
  saved: boolean;
  hidden: boolean;
  not_interested: boolean;
};

export type DocumentSummaryResponse = {
  document_id: UUID;
  sections: Array<{
    section: "core" | "background" | "significance" | "limitations";
    title_ko: string;
    body_ko: string;
  }>;
  generator: "llm" | "source_abstract";
  generated_at: string;
  reason_short: string;
};

export type TopicDocumentsResponse = {
  topic_type: "cso" | "leaf";
  topic_id: UUID;
  items: DocumentSummary[];
  meta: PageMeta;
};

export type InterestTopicView = {
  cso_topic_id: UUID | null;
  leaf_topic_id: UUID | null;
  label: string;
  bucket: InterestBucket;
  is_onboarding_selected: boolean;
};

export type InterestStateResponse = {
  user_id: UUID;
  topics: InterestTopicView[];
  updated_at: string | null;
};

export type TraversalTraceSummary = {
  trace_id: UUID;
  path_labels: string[];
  status: "active" | "stale" | "retracted" | "archived";
  started_active_day: number;
  last_activity_active_day: number;
  leaf_count: number;
};

export type EventResponse = {
  event_id: UUID;
  accepted: boolean;
  server_received_at: string;
  error_code: string | null;
};

export type PageMeta = {
  next_cursor: string | null;
  has_more: boolean;
  page_size: number;
};

export type PagedResponse<T> = {
  items: T[];
  meta: PageMeta;
};

export type ApiTokenStore = {
  getAccessToken: () => Promise<string | null>;
  getRefreshToken: () => Promise<string | null>;
  setTokens: (tokens: TokenPair) => Promise<void>;
  clearTokens: () => Promise<void>;
};

type RequestOptions = {
  auth?: boolean;
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
  retryAuth?: boolean;
};

export class InsightApi {
  private baseUrl: string;
  private tokenStore: ApiTokenStore;

  constructor(baseUrl: string, tokenStore: ApiTokenStore) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.tokenStore = tokenStore;
  }

  private async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const headers: Record<string, string> = {
      Accept: "application/json",
      "Accept-Language": "ko",
      ...(options.headers ?? {})
    };

    if (options.body !== undefined) {
      headers["Content-Type"] = "application/json";
    }

    if (options.auth !== false) {
      const token = await this.tokenStore.getAccessToken();
      if (token) {
        headers.Authorization = `Bearer ${token}`;
      }
    }

    const res = await fetch(`${this.baseUrl}${path}`, {
      method: options.method ?? "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body)
    });

    if (res.status === 204) {
      return undefined as T;
    }

    if (res.status === 401 && options.auth !== false && options.retryAuth !== false) {
      const error = await parseError(res);
      if (error.code === "auth.token_expired" && (await this.tryRefresh())) {
        return this.request<T>(path, { ...options, retryAuth: false });
      }
      throw new ApiError(res.status, error);
    }

    if (!res.ok) {
      throw new ApiError(res.status, await parseError(res));
    }

    return (await res.json()) as T;
  }

  private async tryRefresh(): Promise<boolean> {
    const refreshToken = await this.tokenStore.getRefreshToken();
    if (!refreshToken) {
      return false;
    }
    try {
      const tokens = await this.request<TokenPair>("/auth/refresh", {
        auth: false,
        method: "POST",
        body: { refresh_token: refreshToken }
      });
      await this.tokenStore.setTokens(tokens);
      return true;
    } catch {
      await this.tokenStore.clearTokens();
      return false;
    }
  }

  signup(email: string, password: string): Promise<SignupResponse> {
    return this.request("/auth/signup", {
      auth: false,
      method: "POST",
      body: { email, password }
    });
  }

  login(email: string, password: string): Promise<TokenPair> {
    return this.request("/auth/login", {
      auth: false,
      method: "POST",
      body: { email, password }
    });
  }

  logout(refreshToken: string | null): Promise<void> {
    return this.request("/auth/logout", {
      method: "POST",
      body: { refresh_token: refreshToken }
    });
  }

  me(): Promise<MeResponse> {
    return this.request("/auth/me");
  }

  consentState(): Promise<ConsentStateResponse> {
    return this.request("/consent");
  }

  consentAgree(): Promise<ConsentStateResponse> {
    return this.request("/consent", {
      method: "POST",
      body: { consent_type: "personalization", agreed: true }
    });
  }

  revokeConsent(): Promise<ConsentStateResponse> {
    return this.request("/consent/revoke", {
      method: "POST",
      body: { consent_type: "personalization", confirmation: "confirm" }
    });
  }

  clusters(): Promise<ClustersResponse> {
    return this.request("/topics/cso/clusters");
  }

  submitInterests(csoClusterIds: UUID[], userClass: string): Promise<OnboardingInterestsResponse> {
    return this.request("/onboarding/interests", {
      method: "POST",
      headers: { "X-Idempotency-Key": crypto.randomUUID() },
      body: { cso_cluster_ids: csoClusterIds, user_class: userClass, locale: "ko" }
    });
  }

  coldStartStatus(requestId: UUID): Promise<ColdStartStatusResponse> {
    return this.request(`/onboarding/cold-start-status/${requestId}`);
  }

  dashboard(): Promise<DashboardResponse> {
    return this.request("/recommendations/dashboard");
  }

  refreshDashboard(): Promise<DashboardResponse> {
    return this.request("/recommendations/dashboard/refresh", { method: "POST" });
  }

  documentDetail(documentId: UUID): Promise<DocumentDetailResponse> {
    return this.request(`/documents/${documentId}`);
  }

  documentSummary(documentId: UUID): Promise<DocumentSummaryResponse> {
    return this.request(`/documents/${documentId}/summary`);
  }

  topicDocuments(topicId: UUID): Promise<TopicDocumentsResponse> {
    return this.request(`/topics/${topicId}/documents`);
  }

  interestState(): Promise<InterestStateResponse> {
    return this.request("/interest/state");
  }

  traces(): Promise<PagedResponse<TraversalTraceSummary>> {
    return this.request("/topics/traces");
  }

  postEvent(payload: {
    event_type: EventType;
    document_id?: UUID | null;
    cso_topic_id?: UUID | null;
    leaf_topic_id?: UUID | null;
    dwell_ms?: number | null;
    occurred_at: string;
    client_request_id: string;
  }): Promise<EventResponse> {
    return this.request("/events", { method: "POST", body: payload });
  }

  saveDocument(documentId: UUID): Promise<EventResponse> {
    return this.request("/feedback/save", {
      method: "POST",
      body: { document_id: documentId, client_request_id: crypto.randomUUID() }
    });
  }

  hideDocument(documentId: UUID): Promise<EventResponse> {
    return this.request("/feedback/hide", {
      method: "POST",
      body: { document_id: documentId, client_request_id: crypto.randomUUID() }
    });
  }

  notInterestedDocument(documentId: UUID): Promise<EventResponse> {
    return this.request("/feedback/not-interested", {
      method: "POST",
      body: { document_id: documentId, client_request_id: crypto.randomUUID() }
    });
  }

  savedDocuments(): Promise<PagedResponse<DocumentSummary>> {
    return this.request("/feedback/saved");
  }

  hiddenDocuments(): Promise<PagedResponse<DocumentSummary>> {
    return this.request("/feedback/hidden");
  }

  deleteSaved(documentId: UUID): Promise<void> {
    return this.request(`/feedback/saved/${documentId}`, { method: "DELETE" });
  }

  deleteHidden(documentId: UUID): Promise<void> {
    return this.request(`/feedback/hidden/${documentId}`, { method: "DELETE" });
  }

  deleteNotInterested(documentId: UUID): Promise<void> {
    return this.request(`/feedback/not-interested/${documentId}`, { method: "DELETE" });
  }
}

async function parseError(res: Response): Promise<ErrorResponse> {
  try {
    const body = (await res.json()) as Partial<ErrorResponse> & {
      detail?: Partial<ErrorResponse>;
    };
    const detail = body.detail && typeof body.detail === "object" ? body.detail : body;
    return {
      code: detail.code ?? `http.${res.status}`,
      message: detail.message ?? res.statusText,
      details: detail.details ?? null,
      request_id: detail.request_id ?? null
    };
  } catch {
    return {
      code: `http.${res.status}`,
      message: res.statusText,
      details: null,
      request_id: null
    };
  }
}
