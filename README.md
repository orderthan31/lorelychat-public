# Lorechat

[한국어](README.md) | [English](README.en.md) | [日本語](README.ja.md)

Lorechat은 캐릭터와 세계관을 직접 만들고, 그 설정을 이어가며 대화하는 **셀프 호스팅 캐릭터 채팅 프로젝트**입니다. React 웹 클라이언트와 FastAPI 백엔드를 Docker Compose 하나로 실행할 수 있으며, 휴대폰에서도 편하게 사용할 수 있도록 모바일 화면을 우선해 구성했습니다.

처음 설치할 때는 별도의 API key가 없어도 괜찮습니다. 기본으로 제공하는 mock 모드와 합성 demo 데이터로 화면과 대화 흐름을 먼저 살펴본 뒤, 필요할 때 원하는 LLM provider를 연결하면 됩니다.

> **라이선스를 먼저 확인해 주세요**
> Lorechat은 OSI 승인 오픈소스가 아니라, 소스 코드를 공개한 **source-available 소프트웨어**입니다. 개인 사용을 포함한 허용된 비상업적 용도에는 [PolyForm Noncommercial License 1.0.0](docs/license.ko.md)이 적용됩니다. 별도의 서면 허가 없이 상업적으로 사용할 수 없습니다.

## 이런 기능을 제공합니다

- 캐릭터 카드와 세계관 생성·관리
- 1:1 및 다중 캐릭터 채팅방
- 행동, 대사, 생각, 감정으로 나뉘는 멀티 버블 응답
- 대화 맥락, 장면 상태, 관계와 캐릭터 메모리 유지
- 웹 화면에서 설정하는 LLM provider와 model
- API key 없이 확인할 수 있는 결정론적 mock 모드
- SQLite 데이터 저장, 파일 업로드, 선택적 TTS
- 하나의 주소에서 이용하는 React + Nginx Web과 FastAPI API
- 첫 Docker 실행 시 자동으로 만들어지는 합성 demo 캐릭터와 세계관

## 가장 빠르게 시작하기

### 준비할 것

- Docker Engine
- Docker Compose v2 (`docker compose`)

### 설치하고 실행하기

```bash
git clone <repository-url> lorechat
cd lorechat
cp .env.example .env
docker compose config
docker compose up --build -d
docker compose ps
```

컨테이너가 모두 실행되면 브라우저에서 <http://127.0.0.1:8080>을 열어 주세요.

기본 설정은 `LLM_MOCK=true`입니다. 유료 API를 연결하지 않아도 mock 채팅과 대화 압축 흐름을 바로 확인할 수 있습니다. 다만 TTS처럼 따로 활성화한 외부 기능은 mock 모드와 별개로 외부 요청을 보낼 수 있습니다.

### 정상 실행 여부 확인하기

```bash
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://127.0.0.1:8080/api/ready
```

두 요청이 모두 정상 응답하면 Web과 API가 준비된 상태입니다.

### 종료하고 다시 시작하기

데이터를 그대로 두고 종료하려면 다음 명령을 사용합니다.

```bash
docker compose down
```

다시 시작할 때는 이미지를 새로 만들 필요 없이 다음 명령이면 됩니다.

```bash
docker compose up -d
```

## 데이터는 어디에 저장되나요?

별도의 데이터베이스 서버나 컨테이너는 필요하지 않습니다. API 컨테이너가 `/data/lorechat.db`의 SQLite 파일을 직접 사용하고, Docker named volume이 파일을 보관합니다.

| Compose volume key | 저장하는 내용 |
|---|---|
| `lorechat_data` | SQLite DB와 demo seed 상태 |
| `lorechat_uploads` | 업로드 파일 |
| `lorechat_logs` | 애플리케이션 로그 |
| `lorechat_secrets` | UI에서 저장한 provider credential |

실제 Docker volume 이름 앞에는 Compose project 이름이 붙습니다. 기본 `.env`의 `COMPOSE_PROJECT_NAME=lorechat`을 사용하면 DB volume은 보통 `lorechat_lorechat_data`라는 이름으로 만들어집니다.

`docker compose down`으로 컨테이너를 내리거나 이미지를 업데이트해도 이 데이터는 삭제되지 않습니다.

> **주의:** `docker compose down -v`를 실행하면 SQLite DB, 업로드, 로그, 로컬 provider key가 들어 있는 named volume까지 영구 삭제됩니다. 데이터를 백업했거나 완전히 초기화하려는 경우가 아니라면 `-v`를 붙이지 마세요.

처음 시작할 때는 합성 캐릭터 2명과 기본 세계관이 자동으로 생성됩니다. 빈 화면에서 직접 시작하고 싶다면, 새 data volume을 만들기 전에 `.env`에서 demo seed를 꺼 주세요.

```dotenv
LORECHAT_SEED_DEMO=false
```

## 실제 모델 연결하기

설치 직후에는 mock 모드로 전체 화면이 정상 동작하는지 먼저 확인하는 편이 안전합니다. 확인을 마쳤다면 아래 순서로 실제 모델을 연결할 수 있습니다.

1. 웹 UI에서 **설정 → 모델 설정**을 엽니다.
2. 사용할 provider 계정과 API key를 등록합니다.
3. 모델 목록을 동기화하거나 호환 모델을 직접 추가합니다.
4. 런타임에서 기본으로 사용할 모델을 지정합니다.
5. `.env`에서 mock 모드를 끕니다.

```dotenv
LLM_MOCK=false
```

설정을 저장한 뒤 API 컨테이너를 다시 만들어 주세요.

```bash
docker compose up -d --force-recreate api
```

UI에서 입력한 provider key 원문은 `/secrets/provider_secrets.json`에 평문 JSON으로 저장됩니다. DB에는 key 자체가 아니라 opaque reference ID만 들어갑니다. 이 파일은 POSIX 파일시스템에서 mode `0600`으로 생성되지만, Lorechat이 파일 내용을 따로 암호화하지는 않습니다. Git 저장소나 Docker 이미지에는 포함되지 않으므로 host disk encryption과 접근 제어가 필요하다면 운영 환경에서 함께 설정해 주세요.

Docker 컨테이너 안에서 `127.0.0.1`은 host가 아니라 API 컨테이너 자신을 가리킵니다. host에서 실행 중인 OpenAI-compatible 서버를 연결하려면 기본 예시에 있는 `host.docker.internal`을 사용하세요.

설정값을 더 자세히 보고 싶다면 [Provider 설정](docs/providers.ko.md)과 [환경 설정](docs/configuration.ko.md)을 참고하면 됩니다.

## 자주 확인하는 환경 변수

`.env.example`을 `.env`로 복사해 사용합니다. 실제 key가 들어간 `.env`는 Git에 커밋하지 마세요.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `LORECHAT_BIND_ADDRESS` | `127.0.0.1` | Web endpoint가 바인딩될 host 주소 |
| `LORECHAT_PORT` | `8080` | Web/API 공개 포트 |
| `LLM_MOCK` | `true` | 채팅·압축 생성에 결정론적 mock 응답 사용 |
| `LORECHAT_SEED_DEMO` | `true` | 새 data volume에 합성 demo 데이터 생성 |
| `CORS_ORIGINS` | `http://localhost:8080,http://127.0.0.1:8080` | 직접 API 개발 시 허용할 origin |
| `MEMORY_PROVIDER` | `local` | 메모리 provider 선택 |
| `MEM0_ENABLED` | `false` | 외부 memory integration의 기본 feature flag; 이 값만으로 read/write가 활성화되지는 않음 |

Compose는 컨테이너 내부의 `DATABASE_URL`, `UPLOAD_ROOT`, `LOG_DIR`, `PROVIDER_SECRET_ROOT`를 volume 구조에 맞게 고정합니다. 기본 Compose에는 `MEM0_READ_ENABLED`와 `MEM0_WRITE_ENABLED`가 전달되지 않으므로 외부 memory read/write는 꺼져 있습니다. 외부 메모리를 사용하는 커스텀 배포에서는 provider 의존성과 read/write flag를 직접 구성해야 합니다.

## 외부에 공개하기 전에

기본 배포는 안전하게 `127.0.0.1`에만 바인딩됩니다. Lorechat은 현재 신뢰할 수 있는 단일 사용자가 직접 운영하는 환경을 대상으로 하며, 공개 인터넷용 로그인이나 완전한 인증 경계를 제공하지 않습니다.

다른 기기나 외부 네트워크에서 접속하려면 먼저 다음 중 하나를 준비하세요.

- private VPN
- 인증이 적용된 TLS reverse proxy

별도의 보호 장치 없이 `LORECHAT_BIND_ADDRESS=0.0.0.0`으로 바꾸지 마세요. provider key, SQLite DB, 대화, 프롬프트, 업로드, 로그는 모두 민감 데이터로 다뤄야 합니다.

보안과 관련된 자세한 내용은 [SECURITY.md](SECURITY.ko.md)에 정리되어 있습니다.

## 저장소 둘러보기

```text
apps/web/        React + Vite 웹 클라이언트
apps/api/        FastAPI + SQLModel API
apps/api/seeds/  공개용 합성 demo 데이터
deploy/          Nginx reverse proxy 설정
docs/            아키텍처·설정·셀프 호스팅 문서
docker-compose.yml
```

브라우저는 Nginx가 제공하는 하나의 endpoint에 접속합니다. `/api/*` 요청은 Nginx가 내부 FastAPI 서비스로 전달하며, API 포트 `8123`은 Compose network 안에서만 열립니다.

## 개발 환경에서 실행하기

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

Python 3.12와 [uv](https://docs.astral.sh/uv/) 사용을 권장합니다.

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

## 더 읽어보기

- [아키텍처](docs/architecture.ko.md)
- [환경 설정](docs/configuration.ko.md)
- [Provider 설정](docs/providers.ko.md)
- [셀프 호스팅](docs/self-hosting.ko.md)
- [보안 정책](SECURITY.ko.md)
- [기여 정책](CONTRIBUTING.ko.md)

## 개인정보와 외부 서비스

Lorechat이 기본으로 사용하는 SQLite DB와 업로드 파일은 로컬 Docker volume에 저장됩니다. `LLM_MOCK=true` 상태에서는 채팅과 압축을 위해 외부 LLM을 호출하지 않습니다. 다만 TTS 등 따로 활성화한 기능은 외부 서비스를 사용할 수 있습니다.

실제 LLM provider를 선택하면 응답 생성에 필요한 prompt와 대화 맥락이 해당 provider로 전송됩니다. 기본 Compose에서는 외부 memory read/write가 꺼져 있지만, 커스텀 배포에서 활성화하면 선택한 메모리 정보가 host 밖으로 전송될 수 있습니다.

저장소에 포함된 demo 데이터는 모두 합성 데이터입니다. 실제 대화, persona, 운영 데이터는 들어 있지 않습니다.

## 라이선스

Copyright 2026 orderthan31.

Lorechat은 [PolyForm Noncommercial License 1.0.0](docs/license.ko.md)에 따라 제공됩니다. 이 저장소는 상업적 사용 권한을 부여하지 않습니다. 상업적 이용, 유료 서비스, 재판매, 상업적 호스팅 또는 기업 업무 이용에는 licensor의 별도 서면 허가가 필요합니다.

이 제한 때문에 Lorechat은 OSI 정의의 오픈소스가 아니라 **source-available 프로젝트**입니다.

## 기여와 문의

보안 제보와 bug report는 환영합니다. Contributor/relicensing agreement가 공개되기 전까지 code contribution은 받지 않습니다. 자세한 내용은 [CONTRIBUTING.md](CONTRIBUTING.ko.md)를 참고해 주세요.
