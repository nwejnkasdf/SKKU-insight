"""SKKU InSight backend tests — A2 Phase 0b.

pytest fixtures 는 docker-compose 의 실 postgres + redis 에 분리 DB 로 연결.
TESTING=1 환경에서 DATABASE_URL 자동 swap to `/insight_test`.
"""
