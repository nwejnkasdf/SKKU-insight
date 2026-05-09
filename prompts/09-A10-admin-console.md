# A10 — Admin Console (Phase 3)

> 작업 디렉토리: `/Users/hyojung/학교 과제/소프트웨어공학개론/`
> **사전조건**: Phase 0a A2-stub의 OpenAPI export + admin-console codegen 완료. A2 + A8 권장.

## 너의 역할

운영자용 Next.js 콘솔 (UI-06). 일반 사용자 앱과 분리된 권한·도메인. **codegen된 `admin-console/src/generated/api.ts`만 사용**.

## 첫 5분 — 반드시 read

`prompts/_common-disambiguation.md` "첫 5분" + 다음:

- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/ux/wireframes.md` (UI-06 부분)
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/api/admin.md` (전체)
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/api/collection.md` (admin 부분)
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/ops/admin-bootstrap.md`
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/security/threat-model.md` (E 카테고리)
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/sdd/api-conventions.md`

## 산출

### 1. Next.js 14 App Router 프로젝트
- `admin-console/package.json` (Next.js 14, React 18, TS 5, Tailwind, react-query)
- `admin-console/app/` — App Router pages
- `admin-console/src/generated/api.ts` — codegen된 admin client

### 2. 화면 (UI-06 + 권한별 분기)
- `/admin/login` — 관리자 로그인
- `/admin/force-change-password` — must_change_password=true 시 강제
- `/admin/dashboard` — 운영 통계 (수집 성공률, 낚시성 통계, 토픽 연결 오류)
- `/admin/collection/jobs` — 잡 목록 + 필터 (status, user, source, since)
- `/admin/collection/jobs/[id]` — 잡 상세 + 재실행 버튼
- `/admin/collection/sources` — 소스 레지스트리 + 활성/비활성 토글
- `/admin/clickbait/stats` — 일별 차트
- `/admin/clickbait/results` — 단건 목록
- `/admin/topic-linkage/errors` — 토픽 연결 오류 + 재처리
- `/admin/users` — 사용자 목록 (이메일 부분 마스킹, 권한별)
- `/admin/users/[id]/interest-state` — 사용자 관심 상태 (super/operator만, 점수 노출)
- `/admin/users/[id]/events` — 행동 로그
- `/admin/reprocess-requests` — 재실행 요청 이력

### 3. 권한 매트릭스 (api/admin.md "권한 매트릭스")
- super / operator / read_only 분기
- read_only 는 GET만, 사용자 점수·이메일 마스킹

### 4. JWT aud="admin" 강제
- 토큰 audience 검증
- 사용자 토큰으로 접근 시 403 + redirect

### 5. 데이터 차트
- recharts 또는 chart.js로 일별 통계
- 통계 데이터는 `/admin/clickbait/stats` 에서 24h 캐시된 결과

### 6. SSR 토큰 처리
- httpOnly cookie 또는 client-side localStorage (admin은 단일 디바이스 가정 OK)
- API 호출은 codegen client + Authorization 헤더

## 헌법 (재강조)

- **일반 사용자 토큰으로 admin 접근 시 403** (FR-60, NFR-22, AT-13).
- **must_change_password=true** 시 모든 다른 endpoint 접근 차단 + 강제 비번 변경 화면.
- **사용자 점수·이메일 마스킹**: read_only 권한은 모두 마스킹, operator는 부분 마스킹, super만 전체 노출 (api/admin.md "권한 매트릭스").
- **codegen된 api.ts만 사용**. raw fetch 금지.
- **반응형은 옵션** — 1차는 데스크톱 폭만 (운영 화면은 모바일 사용 가정 X).

## 검증

```bash
docker compose up -d admin-console
# http://localhost:3001 부트
# admin@insight.test 로그인 → 강제 비번 변경 → 대시보드

npm run typecheck      # tsc --strict
npm run lint
npm test               # vitest
npm run build          # next build

# AT-13 검증: 일반 사용자 토큰으로 /admin/* 호출 → 403
curl -H "Authorization: Bearer $USER_TOKEN" http://localhost:8000/admin/users
# 403 + admin.unauthorized
```

테스트:
- super/operator/read_only 권한별 화면 분기
- AT-13 권한 분리 (E2E)
- AT-14 수집 실패 → 재실행 (UC-05)
- 점수·이메일 마스킹 검증

## 출력 형식

기본 + 추가:
- 화면 갯수 + 권한 매트릭스 검증
- AT-13 자동 테스트 통과
- 시연 시나리오 4 (관리자 콘솔 재실행) end-to-end 동작
- 다음 Phase A11이 자동화할 admin endpoint list
