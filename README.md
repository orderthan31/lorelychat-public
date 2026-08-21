# Lorechat

[한국어](README.md) | [English](README.en.md) | [日本語](README.ja.md)

Lorechat은 캐릭터와 세계관을 만들고 대화할 수 있는 **셀프 호스팅·모바일 우선 캐릭터 채팅 런타임**이다. 이 모노레포는 React 웹 클라이언트와 FastAPI 백엔드를 하나의 Docker Compose 배포로 제공한다.

> **라이선스 안내**
> Lorechat은 OSI 승인 오픈소스가 아닌 **source-available 소프트웨어**다. 개인 사용을 포함한 허용된 비상업적 용도에는 [PolyForm Noncommercial License 1.0.0](LICENSE)이 적용된다. 별도의 서면 허가 없이 상업적으로 사용할 수 없다.

## 주요 기능

- 캐릭터 카드와 세계관 생성·관리
- 1:1 및 다중 캐릭터 채팅방
- 행동, 대사, 생각, 감정으로 구성된 멀티 버블 응답
- 대화 맥락, 장면 상태, 관계 및 캐릭터 메모리 유지
- 런타임 provider/model 설정과 결정론적 mock 모드
- SQLite 영속화, 파일 업로드, 선택적 TTS
- 하나의 origin에서 동작하는 React + Nginx Web 및 FastAPI API
- 첫 Docker 실행 시 합성 demo 캐릭터와 세계관 자동 생성

## 빠른 시작

### 요구 사항

- Docker Engine
- Docker Compose v2 (`docker compose`)

### 실행

```bash
git clone <repository-url> lorechat
cd lorechat
cp .env.example .env
docker compose config
docker compose up --build -d
docker compose ps
```

브라우저에서 <http://127.0.0.1:8080>을 연다.

기본값은 `LLM_MOCK=true`다. API key 없이 mock 채팅·압축 흐름을 바로 확인할 수 있다. TTS처럼 별도로 활성화한 외부 기능은 독립적으로 외부 요청을 보낼 수 있다.

### 상태 확인

```bash
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://127.0.0.1:8080/api/ready
```

### 종료 및 재시작

데이터를 유지한 채 종료:

```bash
docker compose down
```

다시 시작:

```bash
docker compose up -d
```

## SQLite와 데이터 보존

별도 데이터베이스 컨테이너는 필요하지 않다. API 컨테이너가 `/data/lorechat.db`의 SQLite 파일을 사용하며, 파일은 Docker named volume에 저장된다.

| Compose volume key | 내용 |
|---|---|
| `lorechat_data` | SQLite DB와 demo seed 상태 |
| `lorechat_uploads` | 업로드 파일 |
| `lorechat_logs` | 애플리케이션 로그 |
| `lorechat_secrets` | UI에서 저장한 provider credential |

실제 Docker volume 이름에는 Compose project prefix가 붙는다. 기본 `.env`의 `COMPOSE_PROJECT_NAME=lorechat`을 사용하면 DB volume은 보통 `lorechat_lorechat_data`로 생성된다.

일반적인 `docker compose down`, 컨테이너 재생성, 이미지 업데이트로는 데이터가 삭제되지 않는다.

> **주의:** `docker compose down -v`는 SQLite DB, 업로드, 로그, 로컬 provider key를 포함한 named volume을 영구 삭제한다. 백업이나 초기화 의도가 있을 때만 실행해야 한다.

첫 시작에서는 합성 캐릭터 2명과 기본 세계관을 생성한다. 새 데이터 volume에서 demo seed를 비활성화하려면 `.env`에 다음 값을 설정한다.

```dotenv
LORECHAT_SEED_DEMO=false
```

## 실제 모델 연결

1. 처음에는 `LLM_MOCK=true`로 실행 상태를 확인한다.
2. 웹 UI에서 **설정 → 모델 설정**을 연다.
3. 사용할 provider 계정과 API key를 등록한다.
4. 모델 목록을 동기화하거나 호환 모델을 직접 추가한다.
5. 런타임 기본 모델을 지정한다.
6. `.env`에서 mock 모드를 끈다.

```dotenv
LLM_MOCK=false
```

7. API 컨테이너를 재생성한다.

```bash
docker compose up -d --force-recreate api
```

UI에서 등록한 provider key 원문은 `/secrets/provider_secrets.json`에 평문 JSON 값으로 저장되고, DB에서는 해당 key를 opaque reference ID로 참조한다. 이 파일은 POSIX 파일시스템에서 mode `0600`으로 생성되지만 Lorechat 자체에서 암호화하지는 않는다. Git 저장소나 Docker 이미지에는 포함되지 않으며, 필요한 경우 host disk encryption과 접근 제어를 함께 사용한다.

Docker 내부에서 `127.0.0.1`은 API 컨테이너 자신을 뜻한다. host의 OpenAI-compatible 서버에 연결할 때는 기본 예시처럼 `host.docker.internal`을 사용한다.

자세한 내용은 [Provider 설정](docs/providers.md)과 [환경 설정](docs/configuration.md)을 참고한다.

## 주요 환경 변수

`.env.example`을 `.env`로 복사해 사용한다. 실제 key가 들어간 `.env`는 커밋하지 않는다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `LORECHAT_BIND_ADDRESS` | `127.0.0.1` | Web endpoint가 바인딩될 host 주소 |
| `LORECHAT_PORT` | `8080` | Web/API 공개 포트 |
| `LLM_MOCK` | `true` | 채팅·압축 생성에 결정론적 mock 응답 사용 |
| `LORECHAT_SEED_DEMO` | `true` | 새 data volume에 합성 demo 데이터 생성 |
| `CORS_ORIGINS` | `http://localhost:8080,http://127.0.0.1:8080` | 직접 API 개발 시 허용할 origin |
| `MEMORY_PROVIDER` | `local` | 메모리 provider 선택 |
| `MEM0_ENABLED` | `false` | 외부 memory integration의 기본 feature flag; 이 값만으로 read/write가 활성화되지는 않음 |

Compose는 컨테이너 내부의 `DATABASE_URL`, `UPLOAD_ROOT`, `LOG_DIR`, `PROVIDER_SECRET_ROOT`를 volume 구조에 맞게 고정한다. 제공된 Compose는 `MEM0_READ_ENABLED`와 `MEM0_WRITE_ENABLED`를 전달하지 않으므로 외부 memory read/write는 비활성 상태다. 커스텀 배포에서만 provider 의존성과 read/write flag를 명시적으로 구성해야 한다.

## 네트워크와 보안

기본 배포는 `127.0.0.1`에만 바인딩된다. 현재 Lorechat은 신뢰할 수 있는 단일 사용자 셀프 호스팅 환경을 대상으로 하며, 완전한 공개 인터넷 인증 경계를 제공하지 않는다.

외부에서 접속하려면 다음 중 하나를 먼저 구성한다.

- private VPN
- 인증이 적용된 TLS reverse proxy

보호 경계 없이 `LORECHAT_BIND_ADDRESS=0.0.0.0`으로 바꾸지 않는다. provider key, SQLite DB, 대화, 프롬프트, 업로드, 로그는 민감 데이터로 취급한다.

자세한 내용은 [SECURITY.md](SECURITY.md)를 참고한다.

## 저장소 구조

```text
apps/web/        React + Vite 웹 클라이언트
apps/api/        FastAPI + SQLModel API
apps/api/seeds/  공개용 합성 demo 데이터
deploy/          Nginx reverse proxy 설정
docs/            아키텍처·설정·셀프 호스팅 문서
docker-compose.yml
```

브라우저는 Nginx의 단일 endpoint에 접속하며 `/api/*` 요청은 내부 FastAPI 서비스로 전달된다. API 포트 `8123`은 Compose network 내부에만 노출된다.

## 로컬 개발 및 검증

### Web

```bash
cd apps/web
npm ci
npm run typecheck
npm run test
npm run build
npm audit --omit=dev
```

### API

Python 3.12와 [uv](https://docs.astral.sh/uv/) 사용을 권장한다.

```bash
cd apps/api
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip sync requirements-dev.txt
python -m pytest -q
```

### Docker

```bash
docker compose config --quiet
docker compose build --pull
docker compose up -d --wait
```

## 문서

- [아키텍처](docs/architecture.md)
- [환경 설정](docs/configuration.md)
- [Provider 설정](docs/providers.md)
- [셀프 호스팅](docs/self-hosting.md)
- [보안 정책](SECURITY.md)
- [기여 정책](CONTRIBUTING.md)

## 개인정보와 외부 서비스

- 기본 runtime 데이터는 로컬 Docker volume에 저장된다.
- `LLM_MOCK=true`이면 채팅·압축 생성에서 외부 LLM을 호출하지 않는다. TTS 등 별도로 활성화한 외부 기능은 독립적으로 요청을 보낼 수 있다.
- 외부 provider를 선택하면 요청에 필요한 prompt와 대화 맥락이 해당 provider로 전송된다.
- 제공된 Compose에서는 외부 memory read/write가 꺼져 있다. 커스텀 배포에서 활성화하면 선택된 메모리 정보가 host 밖으로 전송될 수 있다.
- 포함된 demo 데이터는 합성이며 실제 대화·persona·운영 데이터가 아니다.

## 라이선스

Copyright 2026 orderthan31.

[PolyForm Noncommercial License 1.0.0](LICENSE)에 따라 제공된다. 이 저장소는 상업적 사용 권한을 부여하지 않는다. 상업적 이용, 유료 서비스, 재판매, 상업적 호스팅 또는 기업 업무 이용에는 licensor의 별도 서면 허가가 필요하다.

라이선스 제한 때문에 Lorechat은 OSI 정의의 오픈소스가 아니라 **source-available 프로젝트**다.

## 기여

보안 제보와 bug report는 환영한다. Contributor/relicensing agreement가 공개되기 전까지 code contribution은 받지 않는다. 자세한 내용은 [CONTRIBUTING.md](CONTRIBUTING.md)를 참고한다.
