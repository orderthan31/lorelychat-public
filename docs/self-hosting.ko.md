# 셀프 호스팅

[한국어](self-hosting.ko.md) | [English](self-hosting.md) | [日本語](self-hosting.ja.md)

## 처음 배포하기

```bash
cp .env.example .env
docker compose config
docker compose up --build -d
docker compose ps
curl -fsS http://127.0.0.1:8080/api/ready
```

처음 실행하면 합성 데모 캐릭터와 월드가 생성됩니다. 런타임 데이터는 Docker named volume에 저장됩니다.

## 업데이트

```bash
git pull --ff-only
docker compose build --pull
docker compose up -d
docker compose ps
```

업데이트 전에 영속 데이터를 백업하세요. 데이터가 중요하다면 새 이미지를 스테이징 환경에서 먼저 확인하세요.

## 백업

실제 Compose 볼륨 이름을 먼저 확인합니다.

```bash
docker volume ls --filter label=com.docker.compose.project=lorechat
```

중요한 볼륨은 데이터, 업로드, 로그, 비밀정보입니다. 호스트 백업 도구나 검토된 임시 컨테이너로 보관 파일을 만들고, 백업은 암호화해 저장소 밖에 보관하세요.

## 원격 접속

기본 배포를 공개 인터넷에 바로 노출하지 마세요. 다음 방법 중 하나를 권장합니다.

1. Private VPN 또는 사설 메시 네트워크
2. 요청 크기와 시간 제한을 설정한 인증 TLS 리버스 프록시

이 보호 경계를 먼저 준비한 뒤에 다음 값을 변경하세요.

```dotenv
LORECHAT_BIND_ADDRESS=0.0.0.0
```

## 데모 데이터 초기화

`docker compose down`은 데이터를 보존합니다. Named volume을 제거하면 데이터베이스, 업로드, 로그, 로컬 프로바이더 key가 영구 삭제됩니다. 의도적으로 초기화하고 백업까지 마친 경우가 아니라면 볼륨 제거 명령을 실행하지 마세요.
