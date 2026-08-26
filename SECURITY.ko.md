# 보안 정책

[한국어](SECURITY.ko.md) | [English](SECURITY.md) | [日本語](SECURITY.ja.md)

## 지원 버전

저장소가 공개된 뒤에는 기본 브랜치의 최신 커밋을 기준으로 보안 수정 사항을 제공합니다.

## 취약점 신고

자격 증명, 비공개 대화, 캐릭터 카드, 데이터베이스 또는 취약점 악용 세부 정보를 공개 이슈에 올리지 마세요.

저장소에서 GitHub Private Vulnerability Reporting을 사용할 수 있다면 해당 채널로 신고하세요. 사용할 수 없다면 GitHub 프로필을 통해 저장소 소유자에게 비공개로 연락하고 안전한 신고 채널을 안내받을 때까지 기다려 주세요.

다음 정보를 포함하세요.

- 영향을 받는 버전 또는 커밋
- 배포 구성
- 최소 재현 절차
- 예상 동작과 실제 동작
- 민감한 내용을 제거한 로그

## 배포 경고

Lorechat은 신뢰할 수 있는 single-user self-hosting을 전제로 설계되었습니다. 현재 공개 인터넷에 직접 노출할 수 있는 완전한 인증 경계는 제공하지 않습니다.

- Compose 기본 bind address는 `127.0.0.1`입니다.
- 인증 TLS 리버스 프록시 또는 Private VPN으로 접근을 통제하지 않는다면 bind address를 `0.0.0.0`으로 바꾸지 마세요.
- API 컨테이너는 직접 공개되지 않으며 모든 브라우저 트래픽은 웹 컨테이너를 거칩니다.
- 프로바이더 key, SQLite 데이터베이스, 업로드, 프롬프트, 로그는 민감한 데이터로 취급하세요.
- 업그레이드 전에 Docker 볼륨을 백업하세요.

## 비밀정보 관리

- `.env`, provider-secret JSON, 데이터베이스, 업로드, 로그, 백업을 커밋하지 마세요.
- Git 이력이나 공개 로그에 들어간 적이 있는 자격 증명은 즉시 교체하세요.
- 웹 UI에서 저장한 프로바이더 자격 증명은 mode `0600`의 로컬 파일 기반 비밀정보 저장소에 보관됩니다. 전용 볼륨은 Lorechat이 암호화하지 않습니다.
- 저장 데이터 암호화가 필요한 위협 모델이라면 호스트 수준의 디스크 암호화와 접근 제어를 사용하세요.

## 컨테이너 기본 보안 설정

제공되는 Compose 구성은 non-root 프로세스, read-only 루트 파일시스템, 제거된 Linux capability, `no-new-privileges`, Private named volume, 상태 검사를 사용합니다. Bind mount, device, browser renderer 또는 outbound proxy를 추가하면 이 보안 설정을 다시 검토하세요.
