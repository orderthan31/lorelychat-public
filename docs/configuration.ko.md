# 환경 설정

[한국어](configuration.ko.md) | [English](configuration.md) | [日本語](configuration.ja.md)

`.env.example`을 `.env`로 복사하세요. Docker Compose는 이 파일을 자동으로 읽습니다.

## 핵심 설정값

| 변수 | 기본값 | 용도 |
|---|---:|---|
| `LORECHAT_BIND_ADDRESS` | `127.0.0.1` | 호스트에서 웹 컨테이너를 노출할 주소 |
| `LORECHAT_PORT` | `8080` | 웹/API 엔드포인트에 사용할 호스트 포트 |
| `LLM_MOCK` | `true` | 외부 모델을 호출하지 않고 일정한 응답 사용 |
| `LORECHAT_SEED_DEMO` | `true` | 데이터 볼륨마다 합성 캐릭터와 월드를 한 번 생성 |
| `CORS_ORIGINS` | `http://localhost:8080,http://127.0.0.1:8080` | API를 직접 개발할 때 허용할 오리진 |

Compose 파일은 컨테이너 내부의 `DATABASE_URL`, `UPLOAD_ROOT`, `LOG_DIR`, `PROVIDER_SECRET_ROOT`를 고정합니다. 볼륨 구조까지 함께 다시 설계하는 경우가 아니라면 이 경로를 바꾸지 마세요.

## 프로바이더 환경 변수

일반적으로는 웹 UI의 프로바이더 및 모델 설정을 사용하는 편이 좋습니다. 기존 배포와의 호환을 위해 다음 환경 변수도 지원합니다.

- `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`
- `CHAT_LLM_PROVIDER`, `CHAT_LLM_BASE_URL`, `CHAT_LLM_MODEL`, `CHAT_LLM_API_KEY`
- `GEMINI_API_KEY`

`.env.example`의 빈 값은 의도된 것입니다. 실제 값을 `.env.example`에 넣거나 `.env`를 Git에 커밋하지 마세요.

## 외부 메모리

외부 시맨틱 메모리는 기본적으로 꺼져 있습니다.

```dotenv
MEMORY_PROVIDER=local
MEM0_ENABLED=false
```

기본 Compose 파일은 `MEM0_READ_ENABLED`와 `MEM0_WRITE_ENABLED`를 전달하지 않습니다. 따라서 `MEM0_ENABLED=true`만 설정해도 외부 읽기와 쓰기는 활성화되지 않습니다. 사용자 정의 배포에서는 연동에 필요한 의존성과 읽기/쓰기 플래그를 직접 제공해야 합니다. 이 기능을 켜면 선택한 메모리 내용이 호스트 밖으로 전송될 수 있으므로 프로바이더의 개인정보 처리방침과 보존 설정을 먼저 확인하세요.
