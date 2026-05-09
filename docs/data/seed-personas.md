# 시드 페르소나와 14일 인터랙션 패턴

본 파일은 SKKU InSight 데모/시연을 위한 시드 사용자 페르소나 6명(일반 사용자 5명 + 관리자 1명)과 각 페르소나의 14일치 인터랙션 패턴을 정의한다. `scripts/seed_personas.py`가 본 사양을 코드로 실행한다. 데모 시나리오는 [`../decisions.md`](../decisions.md) §9, AT는 [`../srs/08-acceptance-tests.md`](../srs/08-acceptance-tests.md).

## 페르소나 6명

| ID | 이름(가명) | 클래스 | 선택 CSO | 비밀번호 | 비고 |
|---|---|---|---|---|---|
| persona_01 | 김민수 (Min-su Kim) | 학부생 | AI | `Demo-LLM-2026!` | LLM 관심 학부생 |
| persona_02 | 박지훈 (Ji-hoon Park) | 학부생 | Systems, Networks | `Demo-Sys-2026!` | 시스템 관심 학부생 |
| persona_03 | 이서연 (Seo-yeon Lee) | 연구자 | AI, Graphics·Multimedia | `Demo-VLM-2026!` | VLM 연구자 |
| persona_04 | 정태영 (Tae-young Jung) | 교수 | Systems, Networks, IS·DB | `Demo-Dist-2026!` | 분산시스템 교수 |
| persona_05 | 최하늘 (Ha-neul Choi) | 일반 (학생 외) | AI, HCI | `Demo-Curious-2026!` | 일반 사용자 |
| admin_01 | 운영자 (Operator) | 관리자 | (없음) | `Admin-Bootstrap-2026!` | AT-13 권한 분리 테스트용 |

이메일은 `<id>@insight.test` 형식 (예: `persona_01@insight.test`).

비밀번호는 [`../security/password-policy.md`](../security/password-policy.md) 룰을 만족 (12자 이상 + 다양성).

## 페르소나별 관심 토픽 분포

각 페르소나가 14일 후 도달해야 하는 베이지안 분포 목표.

### persona_01 (LLM 관심 학부생)
- high: AI 클러스터 leaf "Large Language Models", "Prompt Engineering"
- medium: AI 클러스터 leaf "Computer Vision"
- low: SE 클러스터 leaf "Code Generation"

### persona_02 (시스템 관심 학부생)
- high: Systems leaf "Container Orchestration", "Kubernetes"
- medium: Networks leaf "Service Mesh", "eBPF"
- low: Hardware leaf "GPU Architecture"

### persona_03 (VLM 연구자)
- high: AI leaf "Vision-Language Models", Graphics·Multimedia leaf "Diffusion Models"
- medium: AI leaf "Multimodal Learning"
- low: AI leaf "Speech Recognition"

### persona_04 (분산시스템 교수)
- high: Systems leaf "Distributed Consensus", "Database Replication"
- medium: Networks leaf "BGP Security"
- low: Security leaf "Zero Trust"

### persona_05 (일반 사용자)
- medium: AI leaf "Generative AI Trends", HCI leaf "AI UX"
- low: 광범위. Cold-start 후 점진 형성

## 14일 인터랙션 패턴

각 페르소나마다 14일 동안 일정 비율로 다음 이벤트를 생성한다. 시간은 09:00–22:00 KST 분포.

```python
DAILY_PATTERN = {
    "persona_01": {
        "click_per_day": 8,
        "save_per_day": 1,
        "hide_per_day": 0.5,        # 0.5 = 이틀에 1회
        "not_interested_per_week": 1,
        "dwell_avg_seconds": 90,
        "topic_focus": ["Large Language Models", "Prompt Engineering"],
        "noise_topics": ["Quantum Computing"],     # 가끔 노이즈 클릭
    },
    "persona_02": {
        "click_per_day": 6,
        "save_per_day": 2,
        "hide_per_day": 1,
        "not_interested_per_week": 2,
        "dwell_avg_seconds": 150,
        "topic_focus": ["Container Orchestration", "Service Mesh", "eBPF"],
        "noise_topics": ["LLM Fine-tuning"],
    },
    "persona_03": {
        "click_per_day": 5,
        "save_per_day": 3,                          # 연구자 → 저장 많음
        "hide_per_day": 0.2,
        "not_interested_per_week": 0,
        "dwell_avg_seconds": 240,                   # 길게 본다
        "topic_focus": ["Vision-Language Models", "Diffusion Models", "Multimodal Learning"],
        "noise_topics": [],
    },
    "persona_04": {
        "click_per_day": 3,                         # 바쁘다
        "save_per_day": 1.5,
        "hide_per_day": 1.5,                        # 광고성 글 많이 숨김
        "not_interested_per_week": 3,
        "dwell_avg_seconds": 300,
        "topic_focus": ["Distributed Consensus", "Database Replication"],
        "noise_topics": ["TikTok Algorithm"],       # 강한 not_interested 신호
    },
    "persona_05": {
        "click_per_day": 4,
        "save_per_day": 0.3,
        "hide_per_day": 0.5,
        "not_interested_per_week": 0,
        "dwell_avg_seconds": 60,
        "topic_focus": ["Generative AI Trends", "AI UX"],
        "noise_topics": [],
    },
}
```

## 시드 스크립트 의사 코드

`scripts/seed_personas.py`:

```python
import asyncio
from datetime import datetime, timedelta
import random

async def seed_all():
    await create_admin(admin_01)
    for pid, spec in PERSONA_SPECS.items():
        user = await create_user(pid, spec)
        await register_consent(user)
        await complete_onboarding(user, spec.cso_clusters)
        await trigger_cold_start(user)

async def replay_14_days():
    end = datetime.utcnow()
    start = end - timedelta(days=14)
    for pid, pattern in DAILY_PATTERN.items():
        user = await get_user(pid)
        cur = start
        while cur < end:
            day_events = generate_day_events(user, pattern, cur)
            for e in day_events:
                await api.post_event(user, e)
            cur += timedelta(days=1)
        # 일자별 batch 끝마다 daily_decay 트리거 (보통 cron이 처리)

def generate_day_events(user, pattern, day):
    events = []
    # core 토픽 클릭들
    for _ in range(int(pattern["click_per_day"])):
        topic = random.choice(pattern["topic_focus"])
        doc = pick_document_for(topic, day)
        events.append(make_event("click", doc, day_with_jitter(day)))
        # dwell ticks
        ticks = pattern["dwell_avg_seconds"] // 30
        for k in range(ticks):
            events.append(make_event("dwell_tick", doc, day_with_jitter(day, +k*30)))
    # save / hide / not_interested ...
    return events
```

`scripts/seed_personas.py --no-events`는 사용자만 만들고 인터랙션은 생략 (개발 모드 빠른 부트).
`--full`은 14일 인터랙션까지.

## 시연 시나리오 매핑

| 데모 시나리오 (decisions.md §9) | 사용 페르소나 | 검증 AT |
|---|---|---|
| 1. 신규 가입 → 12 클러스터 → cold-start | live signup (persona 미사용) | AT-01, AT-02 |
| 2. 카드 클릭/저장/숨김 → 베이지안 변화 | persona_01 | AT-04, AT-07 |
| 3. 다음 날 시뮬레이션 → emerging → active | persona_03 (긴 dwell) | AT-15 |
| 4. 관리자 콘솔 수집 실패 재실행 | admin_01 | AT-14 |
| 5. 동의 철회 분기 | persona_05 | AT-03 |

권한 분리 테스트 (AT-13)는 persona_01의 token으로 `/admin/*` 호출 → 403.

<!-- TODO: A12가 본 명세를 코드로 옮기면서 실제 사용 가능한 시드 문서 ID들을 결정. cold-start LLM 결과를 fixture로 캡처해서 시연 안정성 확보 -->
