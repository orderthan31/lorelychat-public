# Google Cloud Vertex Gemini (직접 연결)

`vertex_ai`는 Google AI Studio의 `google`과 별도 provider입니다. 현재 범위는 **Gemini 텍스트 채팅과 압축**뿐입니다. Vertex Claude, Model Garden, TTS, Decision, 이미지 입력은 지원하지 않습니다.

## 계정 설정

- `vertex_project`: 요청을 보낼 Google Cloud 프로젝트 ID. 표시 이름, URL, 프로젝트 번호가 아닙니다.
- `vertex_location`: `global` 또는 지역 ID(예: `us-central1`). 입력 형식 검사만 수행하며, 해당 모델의 실제 지역 제공 여부는 보장하지 않습니다. SDK가 다른 지역으로 자동 변경하지 않습니다.
- `vertex_auth_mode`: 생략/null이면 `service_account`. 서버 ADC를 쓰려면 `adc`를 명시해야 합니다.
- `api_key`: `service_account` 모드의 **서비스 계정 JSON 문자열**을 쓰는 기존 write-only 필드입니다. 파일 경로, 짧은 OAuth 토큰, external-account/WIF JSON은 받지 않습니다. 기존 별도 secret store에 저장하며 JSON 일부를 key hint로 표시하지 않습니다.
- `base_url`: 지원하지 않습니다. Google 관리 Vertex 엔드포인트만 사용합니다.

`adc`는 서버 운영자가 준비한 Application Default Credentials를 명시적으로 사용하는 선택입니다. 업로드한 계정 JSON과 혼용하지 않으며, ADC로 전환하면 해당 계정의 저장된 JSON을 폐기합니다. 현재 ADC 범위는 서비스 계정, authorized-user, Google Compute 메타데이터 자격 증명입니다. WIF, impersonation, external-account 및 executable credential 설정은 지원 범위가 아닙니다. 신뢰할 수 있는 서버 운영자만 ADC 환경을 구성해야 합니다.

연결 정보 확인 버튼은 **설정 존재/형식만 확인**합니다. OAuth 인증, IAM 권한, 결제, 모델/지역 제공 여부를 검증하거나 유료 추론을 실행하지 않습니다.

## 모델 선택

자동 `/models` 검색은 제공하지 않습니다. 계정을 만든 다음 Google Cloud에서 이용 가능한 Gemini 모델 ID를 수동 등록하고, 채팅/압축 모델로 직접 선택합니다. `publishers/...` 경로나 `vertex_ai/` 접두사 대신 모델 ID만 입력합니다. 새 계정 생성은 기존 주 모델/대체 모델을 변경하지 않습니다.

## 서버 의존성과 전송

- `LLM_TRANSPORT=litellm`이 필요합니다. 다른 전송 모드에서는 실패 처리하며 API 키 호환 경로로 우회하지 않습니다.
- 유지보수되는 LiteLLM Vertex Gemini adapter가 요청 URL, 헤더, 본문을 구성합니다. 버전 고정된 adapter의 요청별 인스턴스로 인증/지역을 분리하며, 프로세스 전역 Vertex credential cache나 환경 fallback에 업로드 JSON을 넣지 않습니다.
- `google-auth`, `cryptography` 및 관련 잠금 의존성이 필요합니다. 배포 전 애플리케이션의 의존성 잠금/검증 절차로 설치 여부를 확인해야 합니다. 이 변경 자체는 실행 중인 환경에 패키지를 설치하지 않습니다.
- 매 애플리케이션 전송 시도마다 Google-auth가 새 자격 증명을 만들고 OAuth 토큰을 갱신합니다. 계정 간 토큰 캐시를 공유하지 않습니다. 토큰 캐시 최적화는 아직 없습니다.
- 업로드 서비스 계정은 `https://oauth2.googleapis.com/token`만 사용합니다. 인증 HTTP에도 제한 시간, 응답 크기 제한, 리디렉션 차단을 적용합니다. 인증 재시도와 추론 재시도는 SDK 내부에 맡기지 않습니다.
- 기존 추론 재시도/마감시간/회로 차단/대체 모델 정책을 유지합니다. 애플리케이션 시도당 실제 추론 HTTP 전송은 최대 한 번입니다. OAuth 요청은 추론 사용량으로 계산하지 않습니다.
- 원본 응답에서 텍스트, 종료 사유, 모델 버전, 사용량을 읽습니다. 누락/잘못된 사용량은 0으로 만들지 않고 unknown으로 유지합니다. SDK 정규화 실패만으로 이미 받은 유효한 본문을 다시 생성하지 않습니다.

오프라인 MockTransport 테스트는 직렬화/갱신/격리 검증입니다. 실제 Google 인증·IAM·모델 가용성·출력 품질을 검증한 것으로 해석하면 안 됩니다.
