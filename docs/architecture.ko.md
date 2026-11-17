# 아키텍처

[한국어](architecture.ko.md) | [English](architecture.md) | [日本語](architecture.ja.md)

## 런타임 구성

```text
Browser
  |
  v
web (Nginx, :8080)
  |-- /, /assets/*  -> React static files
  `-- /api/*        -> api:8123/*
                         |
                         |-- /data/lorechat.db
                         |-- /uploads
                         |-- /logs
                         `-- /secrets/provider_secrets.json
```

브라우저는 UI와 API를 같은 오리진으로 이용합니다. Nginx는 요청을 FastAPI로 전달하기 전에 `/api` 접두사를 제거합니다. API 포트 `8123`은 Compose 네트워크 안에서만 열립니다.

## 구성 요소

- `apps/web`: React, Vite, TanStack Query/Router, Zustand, Tailwind
- `apps/api`: FastAPI, SQLModel/SQLAlchemy, SQLite, 구조화 생성 파이프라인
- `deploy/nginx.conf`: SPA 라우팅, API 프록시, 업로드 용량 제한, 기본 보안 헤더
- Docker named volume: DB, 업로드, 로그, 프로바이더 비밀정보를 서로 분리해 저장

## 소스 책임 경계

- 웹 `app/`은 provider·layout·기능 controller를 조립한다. `router/`가 TanStack 내비게이션과 기존 URL 매핑을 소유하며 history 소유자는 하나만 둔다.
- `views/`는 화면별 상태·액션, `components/`는 재사용 UI를 소유한다. `agent/`는 별도의 승인·복구 기능이며 일반 CRUD commit과 섞지 않는다.
- 웹 `api/`는 HTTP 계약과 query key를 소유한다. 서버 목록은 Query cache, 편집 초안과 진행 중 생성·복구 상태는 별도 책임으로 둔다. 공유 순수 기본값·정규화는 View에 의존하지 않는다.
- API `main.py`는 lifecycle과 public worker runner를 조립한다. `api/`는 HTTP admission·오류/응답 변환·lifecycle SSE, `services/`는 생성·후처리 실행과 persistence orchestration을 소유한다.
- `engine/`은 prompt·budget·transport·compression 실행 부품, `db/`는 ORM/session·공통 SQL을 소유한다. 목록의 검색·노출·정렬 정책은 도메인 서비스에 둔다. `schemas/`는 외부 계약이다.
- 백엔드 `agent/`는 계획·소유자 승인·CAS·receipt를 소유하고 `mcp/`는 같은 연산의 adapter다. `ops/`는 제한된 운영 I/O·lock·backup·복구를 담당하며 일반 catalog 정책을 담지 않는다.
- provider I/O와 최종화 transaction을 분리한다. 중복처럼 보여도 request journal·unknown 복구·lease/CAS fence·nullable/default suppression 의미를 합쳐 없애지 않는다.

## 시작 과정

1. 데모 seed가 켜져 있고 `/data/.demo-seeded`가 없으면 컨테이너 entrypoint가 `scripts.seed_demo`를 실행한다. 스크립트가 먼저 스키마를 준비하고 이름이 없는 데모 리소스를 생성한 뒤 entrypoint가 볼륨 마커를 남긴다.
2. FastAPI lifespan이 앱·Agent 스키마를 준비하고 Agent service·선택적 MCP gateway를 구성하며 제한된 이미지 cleanup 대기 작업을 복구한다.
3. 기본 채팅 명령을 seed하고 실행 자격이 있는 durable job을 복구한다. 기존 방·세계관 연결과 작성 내용을 가상 세계관으로 대체하지 않는다.
4. 실제 모드는 public service runner로 생성·후처리 dispatcher를 시작한다. mock 모드는 이 실제 dispatcher를 끄는 대신 별도의 감독되는 mock 전용 queue task를 시작한다. 백그라운드 작업이 전혀 없는 모드는 아니다.
5. lifecycle 준비 뒤 `/ready`가 정상 응답하고 Compose web이 API readiness를 기다려 시작한다. 종료 시 admission을 닫고 소유 작업을 drain한 뒤 runtime 소유권을 해제한다.

## 신뢰 경계

- 브라우저 입력과 업로드 파일은 신뢰하지 않는 데이터로 취급합니다.
- 실제 프로바이더를 선택하면 프로바이더 요청이 외부 데이터 경계를 통과합니다.
- 프로바이더 비밀정보 볼륨은 소스 코드가 아니라 민감한 로컬 상태입니다.
- Nginx는 라우팅 계층이며 인증 시스템이 아닙니다.
- 공개 인터넷에 노출하려면 별도의 인증된 TLS 경계가 필요합니다.
