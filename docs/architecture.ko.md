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

## 시작 과정

1. API entrypoint가 데이터 볼륨마다 합성 데모 데이터를 한 번 생성합니다.
2. FastAPI가 SQLite 스키마를 초기화하고 마이그레이션을 적용합니다.
3. 기본 시스템 프롬프트와 채팅 명령어가 만들어집니다.
4. Mock mode에서는 백그라운드 모델 디스패처가 실행되지 않습니다.
5. `/ready`가 정상 상태를 반환한 뒤 웹 서비스가 시작됩니다.

## 신뢰 경계

- 브라우저 입력과 업로드 파일은 신뢰하지 않는 데이터로 취급합니다.
- 실제 프로바이더를 선택하면 프로바이더 요청이 외부 데이터 경계를 통과합니다.
- 프로바이더 비밀정보 볼륨은 소스 코드가 아니라 민감한 로컬 상태입니다.
- Nginx는 라우팅 계층이며 인증 시스템이 아닙니다.
- 공개 인터넷에 노출하려면 별도의 인증된 TLS 경계가 필요합니다.
