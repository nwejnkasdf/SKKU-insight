# SKKU InSight Electron Client

A9 Electron + React + TypeScript 클라이언트입니다. 변경 범위는 `client/` 내부로만 제한합니다.

```bash
cd client
npm install
npm start
```

기본은 mock API 모드입니다. 백엔드 없이 UI 흐름만 테스트할 수 있습니다.

실제 백엔드에 붙일 때는 `.env`에 다음을 둡니다.

```bash
VITE_USE_MOCK_API=false
VITE_API_BASE_URL=http://127.0.0.1:8000
```

mock 모드에서는 가입/로그인/동의/관심 선택/대시보드/문서 상세/저장/숨김/설정이 모두 로컬 상태로 반응합니다.
