# SKKU InSight Electron Client

A9 Electron + React + TypeScript 클라이언트입니다. 변경 범위는 기본적으로 `client/` 내부로 제한합니다.

```bash
cd client
npm install
npm start
```

실제 백엔드 연결은 `.env`에 다음 값을 둡니다.

```bash
VITE_USE_MOCK_API=false
VITE_API_BASE_URL=http://127.0.0.1:8000
```

백엔드 없이 UI 흐름만 확인할 때는 `VITE_USE_MOCK_API=true`로 전환할 수 있습니다. Electron 개발 서버는 `http://127.0.0.1:5173`에서 뜨므로 백엔드 `CORS_ALLOWED_ORIGINS`에 이 origin이 포함되어 있어야 합니다.
