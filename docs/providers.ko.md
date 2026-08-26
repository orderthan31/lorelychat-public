# 프로바이더 설정

[한국어](providers.ko.md) | [English](providers.md) | [日本語](providers.ja.md)

## Mock mode

`LLM_MOCK=true`는 설치 상태를 확인하는 모드입니다. 프로바이더 계정이 필요하지 않고 유료 생성 요청도 보내지 않습니다.

## 웹 UI에서 프로바이더 계정 설정하기

일반적인 설정 순서는 다음과 같습니다.

1. Mock mode로 시작합니다.
2. **설정 → 모델 설정**을 엽니다.
3. 올바른 프로토콜과 base URL로 프로바이더 계정을 만듭니다.
4. API key를 저장합니다.
5. 호환되는 채팅 모델을 동기화하거나 직접 추가합니다.
6. 런타임 기본 모델을 지정합니다.
7. Mock mode를 끄고 API 컨테이너를 다시 만듭니다.

프로바이더 key 원문은 `/secrets/provider_secrets.json`에 평문 JSON으로 저장되고, 데이터베이스 레코드는 불투명한 ID로 이를 참조합니다. POSIX 권한을 지원하는 파일시스템에서는 file mode `0600`을 사용합니다. Lorechat은 API 응답에서 key를 가리지만 비밀정보 파일 자체는 암호화하지 않습니다.

## Local OpenAI-compatible endpoint

Docker 안에서 `127.0.0.1`은 API 컨테이너를 가리킵니다. Docker 호스트의 모델 서버에 연결하려면 다음 값을 사용하세요.

```dotenv
LLM_BASE_URL=http://host.docker.internal:11434/v1
```

Compose 파일은 Linux에서 `host.docker.internal`을 호스트 게이트웨이에 연결합니다. 모델 서버가 Docker에서 접근할 수 있는 주소에서 요청을 받고 있는지, 신뢰하지 않는 네트워크로부터 보호되는지 확인하세요.

## 개인정보

실제 프로바이더는 생성에 필요한 컴파일된 프롬프트와 대화 맥락을 받습니다. 전송 권한이 없는 데이터가 들어 있는 채팅방에는 외부 프로바이더를 연결하지 마세요.
