/*  Live Interest Monitor — admin-console 페이지 컴포넌트.
    탑재 방식: index.html 에서 React/ReactDOM/Babel CDN 로드 후 이 파일을 type="text/babel"로 포함.
    완료 시점에 window.__monitor = { mount, unmount } 가 셋업됨.
    인증/토큰: admin-console 의 글로벌 request() 사용 (자동 refresh 포함). */

(function () {
  const { useState, useMemo, useEffect, useRef, useCallback } = React;

  /* ─── API wrapper — admin-console의 request() 위임 ─── */
  function apiFetch(path, opts = {}) {
    if (typeof window.request !== 'function') {
      return Promise.reject(new Error('admin-console request() not available'));
    }
    return window.request(path, opts);
  }

  const POLL_USERS_MS = 5000;
  const POLL_STATE_MS = 1000;
  const POLL_DOCS_MS  = 5000;

  /* ─── Hash → [0,1) ─── */
  function hash01(str) {
    let h = 2166136261 >>> 0;
    for (let i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619) >>> 0;
    }
    return (h % 100000) / 100000;
  }
  const bucketYCenter = { high: 0.35, med: 0.5, low: 0.65 };
  function layoutTopic(topic) {
    const seed = topic.label || topic.id;
    const a = hash01(seed + ':x');
    const b = hash01(seed + ':y');
    const yC = bucketYCenter[topic.bucket] ?? 0.5;
    return { x: 0.10 + a * 0.80, y: yC - 0.12 + b * 0.24 };
  }

  function relaxPositions(topics) {
    const N = topics.length;
    if (N === 0) return new Map();
    const pts = topics.map(t => {
      const init = (t.x != null && t.y != null) ? { x: t.x, y: t.y } : layoutTopic({ id: t.id, label: t.label, bucket: t.bucket });
      return { id: t.id, x: init.x, y: init.y };
    });
    const minDist = Math.max(0.022, Math.min(0.18, Math.sqrt(0.55 / Math.max(N, 4))));
    const iters = N <= 50 ? 40 : N <= 200 ? 60 : 80;
    for (let iter = 0; iter < iters; iter++) {
      let moved = 0;
      for (let i = 0; i < N; i++) {
        for (let j = i + 1; j < N; j++) {
          const dx = pts[j].x - pts[i].x;
          const dy = pts[j].y - pts[i].y;
          const d = Math.hypot(dx, dy);
          if (d < minDist && d > 1e-6) {
            const push = (minDist - d) * 0.5;
            const nx = dx / d, ny = dy / d;
            pts[i].x -= nx * push; pts[i].y -= ny * push;
            pts[j].x += nx * push; pts[j].y += ny * push;
            moved++;
          } else if (d < 1e-6) {
            pts[j].x += 0.005; pts[j].y += 0.005; moved++;
          }
        }
      }
      for (const p of pts) {
        p.x = Math.max(0.05, Math.min(0.95, p.x));
        p.y = Math.max(0.07, Math.min(0.93, p.y));
      }
      if (moved === 0) break;
    }
    return new Map(pts.map(p => [p.id, { x: p.x, y: p.y }]));
  }

  function nodeScale(N) {
    return Math.min(1.0, Math.max(0.30, Math.sqrt(22 / Math.max(N, 22))));
  }
  function labelableSet(topics, selectedId) {
    const out = new Set();
    if (selectedId) out.add(selectedId);
    const N = topics.length;
    if (N <= 30) {
      for (const t of topics) out.add(t.id);
    } else if (N <= 100) {
      for (const t of topics) if (t.bucket === 'high') out.add(t.id);
      if (out.size < 6) {
        const top = [...topics].sort((a, b) => (b.score ?? 0) - (a.score ?? 0)).slice(0, 8);
        for (const t of top) out.add(t.id);
      }
    } else {
      const top = [...topics].sort((a, b) => (b.score ?? 0) - (a.score ?? 0)).slice(0, 10);
      for (const t of top) out.add(t.id);
    }
    return out;
  }

  function normBucket(raw) {
    if (!raw) return 'low';
    const v = String(raw).toLowerCase();
    if (v === 'high') return 'high';
    if (v === 'medium' || v === 'med') return 'med';
    if (v === 'low') return 'low';
    return 'low';
  }
  function normStatus(raw) {
    if (!raw) return 'stale';
    const v = String(raw).toLowerCase();
    return v === 'active' ? 'active' : v === 'archived' ? 'archived' : 'stale';
  }

  function buildLiveUser(listItem, stateRes, tracesRes) {
    const uid = listItem.user_id;
    const shortId = uid.replace(/-/g, '').slice(0, 6);
    // 기본 노드는 CSO 토픽만. leaf 토픽은 trace 클릭 시 trace detail로 별도 fetch해서 시각 차별 표시.
    const topicsRaw = (stateRes?.topics || [])
      .filter(t => !!t.cso_topic_id)
      .map(t => {
        const id = (t.cso_topic_id || t.label || '').toString();
        const bucket = normBucket(t.bucket);
        const score = Math.max(t.long_score ?? 0, t.short_score ?? 0);
        const onb = !!t.is_onboarding_selected;
        return { id, label: t.label || '(unlabeled)', bucket, score, onb, docs: 0 };
      });
    const seenIds = new Set();
    const topics = [];
    for (const t of topicsRaw) {
      if (seenIds.has(t.id)) continue;
      seenIds.add(t.id);
      topics.push(t);
    }
    const labelToId = Object.fromEntries(topics.map(t => [t.label, t.id]));
    const traces = (tracesRes?.items || tracesRes || []).map(tr => {
      const labels = tr.path_labels || tr.path || [];
      const path = labels.map(lb => labelToId[lb]).filter(Boolean);
      const span = (tr.last_activity_active_day ?? 0) - (tr.started_active_day ?? 0);
      return {
        id: tr.trace_id,
        shortId: (tr.trace_id || '').toString().slice(0, 6),
        label: labels[labels.length - 1] || `trace ${(tr.trace_id || '').toString().slice(0, 6)}`,
        status: normStatus(tr.status),
        path,
        days: Math.max(0, span),
        opLast: '—',
      };
    }).filter(tr => tr.path.length > 0);

    const updatedAt = stateRes?.updated_at ? new Date(stateRes.updated_at) : null;
    const nowMs = Date.now();
    const lastActiveMin = updatedAt ? Math.max(0, Math.round((nowMs - updatedAt.getTime()) / 60000)) : 999;
    const online = lastActiveMin < 5;

    return {
      id: uid,
      shortId,
      persona: listItem.email || '(no email)',
      createdAt: listItem.created_at,
      lastActiveMin,
      online,
      topics,
      traces,
      docs: {},
      live: true,
    };
  }

  /* ─── React hooks: polling ─── */
  function useUserList() {
    const [list, setList] = useState([]);
    const [error, setError] = useState(null);
    const [loaded, setLoaded] = useState(false);
    useEffect(() => {
      let cancelled = false;
      const ac = new AbortController();
      async function tick() {
        try {
          const res = await apiFetch('/admin/users?limit=100', { signal: ac.signal });
          if (cancelled) return;
          setList(res.items || []);
          setError(null);
          setLoaded(true);
        } catch (e) {
          if (cancelled || e.name === 'AbortError') return;
          setError(e);
        }
      }
      tick();
      const i = setInterval(tick, POLL_USERS_MS);
      return () => { cancelled = true; ac.abort(); clearInterval(i); };
    }, []);
    return { list, error, loaded };
  }

  function useUserStateBundle(userIds) {
    const [bundle, setBundle] = useState({});
    const ref = useRef({});
    useEffect(() => {
      if (userIds.length === 0) return;
      let cancelled = false;
      const ac = new AbortController();
      async function pollAll() {
        await Promise.allSettled(userIds.map(async uid => {
          try {
            const [state, traces] = await Promise.all([
              apiFetch(`/admin/users/${uid}/interest-state`, { signal: ac.signal }),
              apiFetch(`/admin/users/${uid}/traces?limit=20`, { signal: ac.signal }),
            ]);
            if (cancelled) return;
            ref.current[uid] = { state, traces };
          } catch (e) {
            if (e.name === 'AbortError') return;
          }
        }));
        if (!cancelled) setBundle({ ...ref.current });
      }
      pollAll();
      const i = setInterval(pollAll, POLL_STATE_MS);
      return () => { cancelled = true; ac.abort(); clearInterval(i); };
    }, [userIds.join('|')]);
    return bundle;
  }

  function useTraceDetail(userId, traceId) {
    const [detail, setDetail] = useState(null);
    useEffect(() => {
      if (!userId || !traceId) { setDetail(null); return; }
      let cancelled = false;
      const ac = new AbortController();
      async function tick() {
        try {
          const res = await apiFetch(`/admin/users/${userId}/traces/${traceId}`, { signal: ac.signal });
          if (cancelled) return;
          setDetail(res);
        } catch (e) {
          if (e.name === 'AbortError') return;
        }
      }
      tick();
      const i = setInterval(tick, 5000);
      return () => { cancelled = true; ac.abort(); clearInterval(i); };
    }, [userId, traceId]);
    return detail;
  }

  function useTopicDocs(userId, topicId) {
    const [docs, setDocs] = useState([]);
    useEffect(() => {
      if (!userId || !topicId) { setDocs([]); return; }
      let cancelled = false;
      const ac = new AbortController();
      async function tick() {
        try {
          const res = await apiFetch(
            `/admin/users/${userId}/topics/${topicId}/documents?limit=20`,
            { signal: ac.signal }
          );
          if (cancelled) return;
          const items = (res.items || []).map(it => ({
            title: it.title || '(no title)',
            conf: it.recommendation_score ?? it.confidence ?? 0,
            evt: 'viewed',
            day: 0,
          }));
          setDocs(items);
        } catch (e) {
          if (e.name === 'AbortError') return;
        }
      }
      tick();
      const i = setInterval(tick, POLL_DOCS_MS);
      return () => { cancelled = true; ac.abort(); clearInterval(i); };
    }, [userId, topicId]);
    return docs;
  }

  /* ─── Bezier helpers ─── */
  const W = 1920, H = 1080;
  const PADDING = 80;
  const px = (n) => PADDING + n * (W - 2 * PADDING);
  const py = (n) => PADDING + n * (H - 2 * PADDING);
  const BUCKET_R    = { high: 11, med: 8, low: 5 };
  const BUCKET_FILL = { high: '#0f766e', med: '#c2410c', low: '#a8a29e' };

  /* ─── Zoom computation: focus camera on trace's bounding box ─── */
  const IDENTITY_ZOOM = { tx: 0, ty: 0, scale: 1 };
  function computeZoom(trace, topicById) {
    if (!trace || !trace.path || trace.path.length === 0) return IDENTITY_ZOOM;
    const pts = trace.path.map(id => topicById[id]).filter(Boolean);
    if (pts.length === 0) return IDENTITY_ZOOM;
    const xs = pts.map(p => px(p.x));
    const ys = pts.map(p => py(p.y));
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const padding = 260;
    const bw = (maxX - minX) + padding * 2;
    const bh = (maxY - minY) + padding * 2;
    const scale = Math.min(W / bw, H / bh, 2.4);
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    return { tx: W / 2 - cx * scale, ty: H / 2 - cy * scale, scale };
  }

  /* ─── Smoothly interpolated zoom group via rAF ─── */
  function ZoomGroup({ target, children }) {
    // Direct DOM transform manipulation via ref — bypasses React re-render storm
    // (60fps × setZoom would re-render the whole graph each frame).
    const gRef = useRef(null);
    const animRef = useRef({ start: { ...target }, target: { ...target }, t0: 0, raf: null });

    useEffect(() => {
      const node = gRef.current;
      if (!node) return;
      const a = animRef.current;
      // Read CURRENT rendered transform (so a re-target mid-animation continues smoothly).
      const ct = node.getAttribute('transform') || '';
      const m = ct.match(/translate\(([\-\d.]+)\s+([\-\d.]+)\)\s*scale\(([\-\d.]+)\)/);
      const cur = m
        ? { tx: parseFloat(m[1]), ty: parseFloat(m[2]), scale: parseFloat(m[3]) }
        : { ...a.start };
      a.start = cur;
      a.target = { ...target };
      a.t0 = performance.now();
      const apply = (v) => {
        if (gRef.current) {
          gRef.current.setAttribute('transform', `translate(${v.tx} ${v.ty}) scale(${v.scale})`);
        }
      };
      apply(cur); // ensure attribute exists from frame 0 (avoid first-paint flash)
      const duration = 520;
      const ease = (t) => 1 - Math.pow(1 - t, 3);
      const step = (now) => {
        const dt = Math.min(1, (now - a.t0) / duration);
        const e = ease(dt);
        apply({
          tx: a.start.tx + (a.target.tx - a.start.tx) * e,
          ty: a.start.ty + (a.target.ty - a.start.ty) * e,
          scale: a.start.scale + (a.target.scale - a.start.scale) * e,
        });
        if (dt < 1) a.raf = requestAnimationFrame(step);
      };
      if (a.raf) cancelAnimationFrame(a.raf);
      a.raf = requestAnimationFrame(step);
      // Fallback for hidden tabs (rAF doesn't fire when document.hidden): snap after 700ms.
      const fallback = setTimeout(() => apply(a.target), 700);
      return () => {
        if (a.raf) cancelAnimationFrame(a.raf);
        clearTimeout(fallback);
      };
    }, [target.tx, target.ty, target.scale]);

    return (
      <g ref={gRef}>
        {children}
      </g>
    );
  }

  /* ─── Group trace's leaves by parent CSO node, lay out radially around each ─── */
  function placeLeaves(trace, leaves, topicById) {
    if (!trace || !leaves || leaves.length === 0) return [];
    const groups = new Map();
    const fallbackParent = trace.path[trace.path.length - 1];
    for (const leaf of leaves) {
      const parent = (leaf.cso_topic_ids || []).find(cid => topicById[cid]) || fallbackParent;
      if (!groups.has(parent)) groups.set(parent, []);
      groups.get(parent).push(leaf);
    }
    const out = [];
    for (const [parentId, group] of groups) {
      const parent = topicById[parentId];
      if (!parent) continue;
      const cx = px(parent.x), cy = py(parent.y);
      const count = group.length;
      const radius = 46 + Math.min(count, 6) * 5;
      group.forEach((leaf, i) => {
        const angle = (i / Math.max(count, 1)) * 2 * Math.PI - Math.PI / 2;
        out.push({
          leaf_topic_id: leaf.leaf_topic_id,
          label: leaf.label,
          confidence: leaf.confidence,
          parentId,
          parentX: cx,
          parentY: cy,
          x: cx + radius * Math.cos(angle),
          y: cy + radius * Math.sin(angle),
        });
      });
    }
    return out;
  }

  function quadCurve(p1, p2) {
    const mx = (p1.x + p2.x) / 2;
    const my = (p1.y + p2.y) / 2;
    const dx = p2.x - p1.x;
    const dy = p2.y - p1.y;
    const len = Math.hypot(dx, dy) || 1;
    const offset = Math.min(len * 0.10, 22);
    const nx = -dy / len, ny = dx / len;
    const cx = mx + nx * offset;
    const cy = my + ny * offset;
    return `M${p1.x},${p1.y} Q${cx},${cy} ${p2.x},${p2.y}`;
  }
  function tracePath(trace, topicById) {
    const pts = trace.path.map(id => {
      const t = topicById[id];
      return { x: px(t.x), y: py(t.y) };
    });
    if (pts.length < 2) return '';
    let d = '';
    for (let i = 0; i < pts.length - 1; i++) {
      const seg = quadCurve(pts[i], pts[i + 1]);
      d += (i === 0 ? seg : seg.replace(/^M[^Q]+/, ' '));
    }
    return d;
  }

  /* ═══════════════════ COMPONENTS ═══════════════════ */
  function Header({ user, now, liveStatus, adminEmail }) {
    const fmt = now.toLocaleTimeString('en-GB', { hour12: false });
    const pillCls = liveStatus === 'err' ? 'err' : '';
    const pillLabel = liveStatus === 'err' ? 'API ERR' : 'LIVE';
    return (
      <div className="m-header">
        <div className="m-brand">
          <div className="m-brand-mark" />
          <div>
            <div className="m-brand-title">Interest Monitor <span className="dim">/ admin</span></div>
            <div className="m-brand-sub">{adminEmail || 'admin'} · live topic graph</div>
          </div>
        </div>
        <div className="m-header-spacer" />
        <div className="m-header-meta">
          <div className="m-meta-cell"><div className="m-meta-label">User</div><div className="m-meta-value">#{user.shortId}</div></div>
          <div className="m-meta-cell"><div className="m-meta-label">Topics</div><div className="m-meta-value">{user.topics.length}</div></div>
          <div className="m-meta-cell"><div className="m-meta-label">Traces</div><div className="m-meta-value">{user.traces.filter(t => t.status === 'active').length}</div></div>
          <div className="m-meta-cell"><div className="m-meta-label">Last Update</div><div className="m-meta-value">{fmt}</div></div>
          <div className={`m-live-pill ${pillCls}`}><span className="m-live-dot" /> {pillLabel}</div>
        </div>
      </div>
    );
  }

  function UsersPanel({ users, selectedId, onSelect, freshIds }) {
    return (
      <div className="m-panel-users">
        <div className="m-panel-title"><span>Users</span><span className="m-count-pill">{users.length}</span></div>
        {users.map(u => {
          const sel = selectedId === u.id;
          const active = u.traces.filter(t => t.status === 'active').length;
          const fresh = freshIds && freshIds.has(u.id);
          return (
            <div
              key={u.id}
              className={`m-user-card ${sel ? 'selected' : ''} ${fresh ? 'fresh' : ''}`}
              onClick={() => onSelect(u.id)}
            >
              <div className="m-uc-row1">
                <span className="m-uc-id">#{u.shortId}</span>
                <span className={`m-uc-active-mark ${u.online ? '' : 'idle'}`} />
              </div>
              <div className="m-uc-persona">{u.persona}</div>
              <div className="m-uc-stats">
                <span><span className="k">topic</span> {u.topics.length}</span>
                <span><span className="k">trace</span> {active}</span>
                <span><span className="k">live</span> {u.lastActiveMin < 999 ? `${u.lastActiveMin}m` : '—'}</span>
              </div>
            </div>
          );
        })}
        {users.length === 0 && (
          <div className="m-focus-empty">사용자 데이터를 불러오는 중…</div>
        )}
      </div>
    );
  }

  function Graph({ user, selectedTopic, selectedTrace, onTopicSelect, traceDetail }) {
    const N = user.topics.length;
    const posKey = user.id + '|' + user.topics.map(t => t.id).sort().join(',');
    const positions = useMemo(() => relaxPositions(user.topics), [posKey]);
    const topicById = useMemo(() => {
      const map = {};
      for (const t of user.topics) {
        const p = positions.get(t.id) || { x: t.x ?? 0.5, y: t.y ?? 0.5 };
        map[t.id] = { ...t, x: p.x, y: p.y };
      }
      return map;
    }, [user.topics, positions]);
    const scale = useMemo(() => nodeScale(N), [N]);
    const labelable = useMemo(() => labelableSet(user.topics, selectedTopic), [user.topics, selectedTopic]);

    const selectedTr = useMemo(
      () => (selectedTrace ? user.traces.find(t => t.id === selectedTrace) : null),
      [selectedTrace, user.traces]
    );
    const zoomTarget = useMemo(
      () => (selectedTr ? computeZoom(selectedTr, topicById) : IDENTITY_ZOOM),
      [selectedTr, topicById]
    );
    const leaves = useMemo(
      () => placeLeaves(selectedTr, traceDetail?.leaves || [], topicById),
      [selectedTr, traceDetail, topicById]
    );

    return (
      <div className="m-panel-graph">
        <div className="m-graph-meta">
          <div className="gm-1">{user.persona}</div>
          <div className="gm-2">#{user.shortId}</div>
        </div>
        <svg className="m-graph-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
          <ZoomGroup target={zoomTarget}>
            {/* Edges */}
            {user.traces.map(tr => {
              const d = tracePath(tr, topicById);
              if (!d) return null;
              const isSel = selectedTrace === tr.id;
              return (
                <path key={tr.id} d={d} className={`m-edge ${tr.status} ${isSel ? 'selected' : ''}`} />
              );
            })}
            {/* Nodes */}
            {user.topics.map(t => {
              const placed = topicById[t.id];
              const cx = px(placed.x), cy = py(placed.y);
              const r = Math.max(2.4, BUCKET_R[t.bucket] * scale);
              const fill = BUCKET_FILL[t.bucket];
              const sel = selectedTopic === t.id;
              const showLabel = labelable.has(t.id);
              return (
                <g
                  key={t.id}
                  className={`m-node-group ${sel ? 'selected' : ''}`}
                  onClick={() => onTopicSelect(t.id)}
                >
                  {t.onb && <circle className="m-node-onb" cx={cx} cy={cy} r={r + 5} />}
                  <circle className="m-node-core" cx={cx} cy={cy} r={r} fill={fill} />
                  <text
                    className={`m-node-label ${t.bucket === 'high' ? 'hi' : t.bucket === 'low' ? 'dim' : ''} ${showLabel ? '' : 'hidden'}`}
                    x={cx}
                    y={cy + r + Math.max(11, 15 * scale)}
                    textAnchor="middle"
                  >
                    {t.label}
                  </text>
                </g>
              );
            })}
            {/* Leaves — selected trace's dynamic leaf topics, attached to their parent CSO node */}
            {leaves.map((leaf, i) => (
              <g key={leaf.leaf_topic_id || i} className="m-leaf-group">
                <line
                  className="m-leaf-link"
                  x1={leaf.parentX} y1={leaf.parentY}
                  x2={leaf.x} y2={leaf.y}
                />
                <path
                  className="m-leaf-diamond"
                  d={`M${leaf.x},${leaf.y - 6.5} L${leaf.x + 6.5},${leaf.y} L${leaf.x},${leaf.y + 6.5} L${leaf.x - 6.5},${leaf.y} Z`}
                />
                <text
                  className="m-leaf-label"
                  x={leaf.x}
                  y={leaf.y + 16}
                  textAnchor="middle"
                >
                  {leaf.label}
                </text>
              </g>
            ))}
          </ZoomGroup>
        </svg>
        <div className="m-graph-legend">
          <div className="m-lg-item"><span className="m-lg-dot high" /> high</div>
          <div className="m-lg-item"><span className="m-lg-dot med" /> med</div>
          <div className="m-lg-item"><span className="m-lg-dot low" /> low</div>
          <div className="m-lg-item" style={{ marginLeft: 12 }}><span className="m-lg-line active" /> active trace</div>
          <div className="m-lg-item"><span className="m-lg-line stale" /> stale</div>
          <div className="m-lg-item" style={{ marginLeft: 12 }}><span className="m-lg-leaf" /> leaf</div>
          <div className="m-lg-item" style={{ marginLeft: 12, color: 'var(--m-text-3)' }}>○ onboarding</div>
          <div className="m-lg-item" style={{ marginLeft: 12, color: 'var(--m-text-3)' }}>N={N}{leaves.length ? ` · leaves=${leaves.length}` : ''}</div>
        </div>
      </div>
    );
  }

  function DetailPanel({ user, selectedTopic, selectedTrace, onTraceSelect, docs }) {
    const topic = selectedTopic ? user.topics.find(t => t.id === selectedTopic) : null;
    const docList = docs || [];
    return (
      <div className="m-panel-detail">
        <div className="m-traces-list">
          <div className="m-panel-title"><span>Traces</span><span className="m-count-pill">{user.traces.length}</span></div>
          {user.traces.length === 0 && <div className="m-focus-empty">trace 없음</div>}
          {user.traces.map(tr => {
            const sel = selectedTrace === tr.id;
            return (
              <div key={tr.id} className={`m-trace-row ${tr.status} ${sel ? 'selected' : ''}`} onClick={() => onTraceSelect(tr.id)}>
                <div className="m-tr-head">
                  <span className="m-tr-id">#{String(tr.id).slice(0, 6)}</span>
                  <span className={`m-tr-status ${tr.status}`}>{tr.status}</span>
                </div>
                <div className="m-tr-label">{tr.label}</div>
                <div className="m-tr-meta">
                  <span><span className="k">hops</span> {tr.path.length}</span>
                  <span><span className="k">days</span> {tr.days}</span>
                </div>
              </div>
            );
          })}
        </div>
        <div className="m-focus-divider" />
        {!topic && (
          <>
            <div className="m-panel-title"><span>Focus</span></div>
            <div className="m-focus-empty">노드 또는 trace를 클릭하면 토픽 상세와 수집된 문서가 여기 표시됩니다.</div>
          </>
        )}
        {topic && (
          <>
            <div className="m-panel-title"><span>Focus</span><span className="m-count-pill mono">#{String(topic.id).slice(0, 6)}</span></div>
            <div className="m-focus-head">
              <div className="m-focus-label">{topic.label}</div>
            </div>
            <div className="m-focus-stats">
              <div className="m-focus-row">
                <span className="k">Bucket</span>
                <span className={`m-bucket-chip ${topic.bucket === 'high' ? 'high' : topic.bucket === 'med' ? 'med' : 'low'}`}>{topic.bucket}</span>
              </div>
              <div className="m-focus-row"><span className="k">Score</span><span className="v">{(topic.score ?? 0).toFixed(2)}</span></div>
              <div className="m-focus-row"><span className="k">Onboarding</span><span className="v">{topic.onb ? 'YES' : '—'}</span></div>
              <div className="m-focus-row"><span className="k">Docs</span><span className="v">{docList.length}</span></div>
            </div>
            <div className="m-docs-title"><span>Collected Documents</span><span>{docList.length}</span></div>
            {docList.length === 0 && <div className="m-focus-empty">아직 수집된 문서 없음.</div>}
            {docList.map((d, i) => (
              <div className="m-doc-row" key={i}>
                <span className={`m-doc-icon ${d.evt}`} />
                <div className="m-doc-body">
                  <div className="m-doc-title">{d.title}</div>
                  <div className="m-doc-meta">
                    <span>{d.evt}</span>
                    <span className="conf">conf {(d.conf ?? 0).toFixed(2)}</span>
                  </div>
                </div>
              </div>
            ))}
          </>
        )}
      </div>
    );
  }

  /* ═══════════════════ APP ═══════════════════ */
  function MonitorApp({ adminEmail }) {
    const [now, setNow] = useState(() => new Date());
    useEffect(() => {
      const i = setInterval(() => setNow(new Date()), 1000);
      return () => clearInterval(i);
    }, []);

    const { list: liveList, error: listErr } = useUserList();
    const liveUserIds = useMemo(
      () => liveList.map(u => u.user_id),
      [liveList.map(u => u.user_id).join('|')]
    );
    const bundle = useUserStateBundle(liveUserIds);

    const users = useMemo(() => {
      return liveList.map(item => {
        const slot = bundle[item.user_id] || {};
        return buildLiveUser(item, slot.state || { topics: [] }, slot.traces || { items: [] });
      });
    }, [liveList, bundle]);

    // Fresh-arrival animation
    const seenIdsRef = useRef(new Set());
    const [freshIds, setFreshIds] = useState(new Set());
    useEffect(() => {
      if (!users.length) return;
      const incoming = new Set(users.map(u => u.id));
      const fresh = new Set();
      for (const id of incoming) if (!seenIdsRef.current.has(id)) fresh.add(id);
      seenIdsRef.current = incoming;
      if (fresh.size) {
        setFreshIds(fresh);
        const t = setTimeout(() => setFreshIds(new Set()), 2500);
        return () => clearTimeout(t);
      }
    }, [users]);

    const liveStatus = listErr ? 'err' : 'live';

    // Selection
    const [selectedUserId, setSelectedUserId] = useState(null);
    const [selectedTopic, setSelectedTopic] = useState(null);
    const [selectedTrace, setSelectedTrace] = useState(null);

    useEffect(() => {
      if (users.length === 0) return;
      if (!users.find(u => u.id === selectedUserId)) {
        const first = users[0];
        setSelectedUserId(first.id);
        const firstActive = first.traces.find(t => t.status === 'active') || first.traces[0];
        setSelectedTrace(firstActive?.id ?? null);
        if (firstActive && firstActive.path.length) {
          setSelectedTopic(firstActive.path[firstActive.path.length - 1]);
        } else {
          setSelectedTopic(first.topics[0]?.id ?? null);
        }
      }
    }, [users, selectedUserId]);

    const user = users.find(u => u.id === selectedUserId) || users[0];
    const docs = useTopicDocs(selectedUserId, selectedTopic);
    const traceDetail = useTraceDetail(selectedUserId, selectedTrace);

    const handleUserSelect = (id) => {
      setSelectedUserId(id);
      const u = users.find(x => x.id === id);
      if (u) {
        const firstActive = u.traces.find(t => t.status === 'active') || u.traces[0];
        setSelectedTrace(firstActive?.id ?? null);
        if (firstActive && firstActive.path.length) {
          setSelectedTopic(firstActive.path[firstActive.path.length - 1]);
        } else {
          setSelectedTopic(u.topics[0]?.id ?? null);
        }
      }
    };
    const handleTraceSelect = (id) => {
      setSelectedTrace(id);
      const tr = user?.traces.find(t => t.id === id);
      if (tr && tr.path.length) setSelectedTopic(tr.path[tr.path.length - 1]);
    };

    if (!user) {
      return (
        <div className="m-app">
          <Header
            user={{ shortId: '——', topics: [], traces: [] }}
            now={now}
            liveStatus={liveStatus}
            adminEmail={adminEmail}
          />
          <div className="m-panel-users">
            <div className="m-panel-title"><span>Users</span><span className="m-count-pill">0</span></div>
            <div className="m-focus-empty">{listErr ? `API 오류 — ${listErr.message || listErr.status}` : '사용자 목록을 가져오는 중…'}</div>
          </div>
          <div className="m-panel-graph" />
          <div className="m-panel-detail" />
        </div>
      );
    }

    return (
      <div className="m-app">
        <Header user={user} now={now} liveStatus={liveStatus} adminEmail={adminEmail} />
        <UsersPanel users={users} selectedId={selectedUserId} onSelect={handleUserSelect} freshIds={freshIds} />
        <Graph user={user} selectedTopic={selectedTopic} selectedTrace={selectedTrace} onTopicSelect={setSelectedTopic} traceDetail={traceDetail} />
        <DetailPanel user={user} selectedTopic={selectedTopic} selectedTrace={selectedTrace} onTraceSelect={handleTraceSelect} docs={docs} />
      </div>
    );
  }

  /* ═══════════════════ mount API ═══════════════════ */
  let currentRoot = null;
  let currentEl = null;
  function mount(el, opts = {}) {
    if (!el) return;
    if (currentEl === el && currentRoot) {
      // already mounted on same element — just re-render with new opts
      currentRoot.render(<MonitorApp adminEmail={opts.adminEmail || ''} />);
      return;
    }
    unmount();
    currentEl = el;
    currentRoot = ReactDOM.createRoot(el);
    currentRoot.render(<MonitorApp adminEmail={opts.adminEmail || ''} />);
  }
  function unmount() {
    if (currentRoot) {
      try { currentRoot.unmount(); } catch {}
      currentRoot = null;
      currentEl = null;
    }
  }
  window.__monitor = { mount, unmount };
})();
