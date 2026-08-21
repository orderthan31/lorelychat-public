const viteEnv = (import.meta as ImportMeta & { env?: { VITE_API_BASE_URL?: string } }).env;
export const API_BASE: string = viteEnv?.VITE_API_BASE_URL || '/api';
export const USER_ID = 'user_001';
export const SYSTEM_ID = 'system';
export const LIST_PAGE_SIZE = 8;
export const MESSAGE_PAGE_TURNS = 10;
export const BUBBLE_REVEAL_DELAY_MS = 3000;
export const DEFAULT_FORBIDDEN_RULES = ['다른 캐릭터의 대사를 대신 쓰지 않는다.', '사용자의 행동이나 감정을 대신 결정하지 않는다.'];

export type TraitDefinition = { key: string; label: string; hint: string };
export const TRAIT_DEFINITIONS: TraitDefinition[] = [
  { key: 'confidence', label: '자신감', hint: '자기확신, 밀어붙이는 힘' },
  { key: 'kindness', label: '다정함', hint: '배려와 부드러운 반응' },
  { key: 'jealousy', label: '질투심', hint: '소유욕, 경쟁심, 예민함' },
  { key: 'eros', label: '에로스', hint: '관능적 긴장감과 플러팅 농도' },
  { key: 'aggression', label: '공격성', hint: '직설, 압박, 반격 성향' },
  { key: 'playfulness', label: '장난기', hint: '놀림, 농담, 가벼운 도발' },
  { key: 'shyness', label: '수줍음', hint: '머뭇거림, 방어적 반응' },
  { key: 'initiative', label: '주도성', hint: '대화/행동을 먼저 이끄는 정도' },
];
export const DEFAULT_TRAIT_SCORES: Record<string, number> = Object.fromEntries(TRAIT_DEFINITIONS.map((trait) => [trait.key, 3]));

export type GenreModeOption = { key: string; label: string; hint: string };
export const GENRE_MODE_OPTIONS: GenreModeOption[] = [
  { key: 'battle', label: '배틀', hint: '도장깨기, 리그, 승패/서열/결과 장부 중심' },
  { key: 'romance', label: '연애', hint: '감정선, 약속, 질투, 독점욕, 관계 변화 중심' },
  { key: 'fantasy', label: '판타지', hint: '퀘스트, 세계관, 아이템, 세력 변화 중심' },
  { key: 'slice_of_life', label: '일상', hint: '일정, 생활 루틴, 가벼운 관계 흐름 중심' },
  { key: 'mystery', label: '미스터리', hint: '단서, 가설, 용의자, 미해결 의문 중심' },
  { key: 'custom', label: '커스텀', hint: '세계관/압축포인트를 우선 따르는 방' },
];
export const DEFAULT_GENRE_MODE = 'battle';
export const ROOM_TONE_PRESETS = [
  { key: '', label: '기본 톤', hint: '캐릭터 말투와 방 세계관을 우선 사용' },
  { key: 'fast_banter', label: '빠른 티키타카', hint: '짧고 빠른 리액션, 장난, 낮은 설명 밀도' },
  { key: 'slow_burn', label: '느린 감정선', hint: '저항감, 여운, 늦게 풀리는 보상감' },
  { key: 'cinematic', label: '시네마틱 묘사', hint: '감각 묘사와 장면 전환을 더 선명하게' },
  { key: 'strategy', label: '전략/심리전', hint: '계산, 압박, 선택의 결과를 더 강하게' },
];
export const CAST_ROLE_PRESETS = [
  { key: '', label: '선택 없음', hint: '캐릭터 기본 말맛과 장면 흐름에 맡긴다.' },
  { key: 'primary', label: '메인 캐릭터', hint: '관계 중심축. 가장 먼저 반응하고 감정선을 끌고 간다.' },
  { key: 'rival', label: '라이벌·견제자', hint: '질투, 압박, 경쟁심으로 긴장감을 만든다.' },
  { key: 'support', label: '조력자', hint: '메인 감정선을 보조하고 상황을 정리한다.' },
  { key: 'observer', label: '관찰자', hint: '과발화하지 않고 짧게 개입하며 흐름을 지켜본다.' },
  { key: 'silent', label: '말하지 않음', hint: '방에는 남아있지만 자동 응답 대상에서 제외한다.' },
  { key: 'antagonist', label: '방해자', hint: '갈등과 방해를 만들되 장면 규칙은 지킨다.' },
  { key: 'comic_relief', label: '분위기 메이커', hint: '가벼운 농담과 리액션으로 템포를 살린다.' },
];
export const RELATIONSHIP_ARCHETYPE_PRESETS = [
  { key: '', label: '기본 관계', hint: '캐릭터 말투와 방 설정을 그대로 사용' },
  { key: 'guarded_slowburn', label: '경계심 · 슬로우번', hint: '작은 반응은 주지만 안정적 신뢰는 늦게' },
  { key: 'high_affection_clingy', label: '높은 호감 · 매달림', hint: '먼저 다가오지만 관계 경계는 남김' },
  { key: 'obsessive_low_trust', label: '집착 높음 · 신뢰 낮음', hint: '강하게 신경 쓰지만 의심과 테스트가 섞임' },
  { key: 'tsundere_hidden_affection', label: '츤데레 · 숨은 호감', hint: '겉으론 튕기고 속으론 반응하는 공략맛 프로필' },
  { key: 'rival_to_lovers', label: '라이벌 · 호감 전환', hint: '경쟁과 인정이 감정 보상으로 이어짐' },
  { key: 'wounded_defensive', label: '상처 있음 · 방어적', hint: '쉽게 열리지 않고 일관성에 조금씩 풀림' },
  { key: 'manipulative_tease', label: '밀당 · 장난스러운 조종', hint: '놀림, 테스트, 전략적 부드러움' },
];
export const EMPTY_PRESET = { preset_type: 'speech_style', title: '', content: '', description: '', enabled: true };
export const PRESET_TYPE_LABELS: Record<string, string> = { speech_style: '말투' };
export const DATA_CLEANUP_CATEGORIES = [
  ['memory', '메모리'], ['relationship', '관계'], ['conversation', '대화방'], ['asset', '에셋'], ['usage', '사용량'],
];

export const EMPTY_CHARACTER = { id: '', name: '', description: '', persona: '', appearance: '', behavior_style: '', speech_style: '', emotional_rules: [] as string[], forbidden_rules: DEFAULT_FORBIDDEN_RULES, default_model: '', avatar_url: '', trait_scores: DEFAULT_TRAIT_SCORES, tts_provider: 'supertonic', tts_model: 'supertonic-3', tts_voice_style: 'F1', tts_sample_text: '안녕하세요. 이 목소리가 캐릭터와 잘 어울리는지 확인해 주세요.' };
export const FALLBACK_TTS_VOICES = [
  { key: 'F1', label: 'Supertonic F1 · 밝고 선명한 여성', provider: 'supertonic' }, { key: 'F2', label: 'Supertonic F2 · 부드러운 여성', provider: 'supertonic' }, { key: 'F3', label: 'Supertonic F3 · 차분한 여성', provider: 'supertonic' }, { key: 'F4', label: 'Supertonic F4 · 또렷한 여성', provider: 'supertonic' }, { key: 'F5', label: 'Supertonic F5 · 낮고 안정적인 여성', provider: 'supertonic' },
  { key: 'M1', label: 'Supertonic M1 · 밝고 선명한 남성', provider: 'supertonic' }, { key: 'M2', label: 'Supertonic M2 · 부드러운 남성', provider: 'supertonic' }, { key: 'M3', label: 'Supertonic M3 · 차분한 남성', provider: 'supertonic' }, { key: 'M4', label: 'Supertonic M4 · 또렷한 남성', provider: 'supertonic' }, { key: 'M5', label: 'Supertonic M5 · 낮고 안정적인 남성', provider: 'supertonic' },
  { key: 'Kore', label: 'Gemini Kore · Firm', provider: 'gemini' }, { key: 'Aoede', label: 'Gemini Aoede · Breezy', provider: 'gemini' }, { key: 'Leda', label: 'Gemini Leda · Youthful', provider: 'gemini' }, { key: 'Achernar', label: 'Gemini Achernar · Soft', provider: 'gemini' }, { key: 'Sulafat', label: 'Gemini Sulafat · Warm', provider: 'gemini' },
];
export const FALLBACK_TTS_MODELS = [
  { key: 'supertonic-3', label: 'Supertonic 3 · 로컬/무료', provider: 'supertonic', cost_hint: '로컬 CPU 사용' },
  { key: 'gemini-3.1-flash-tts-preview', label: 'Gemini 3.1 Flash TTS Preview · 고품질/API', provider: 'gemini', cost_hint: 'Google API 과금' },
];

export type RuntimeSettingDefaults = { model_key: string | null; fallback_model_key: string | null; compression_model_key: string | null; compression_fallback_model_key: string | null; effective_compression_fallback_model_key: string | null; compression_fallback_source: string; response_length_preset: string; min_output_tokens: number; compression_interval_turns: number; options: unknown[]; compression_options: unknown[]; response_length_presets: unknown[]; compression_interval_options: unknown[] };
export const FALLBACK_COMPRESSION_INTERVAL_OPTIONS = [
  { turns: 2, label: '2턴마다 · 강한 기억 유지', description: '새 방/관계 초반처럼 첫 상황과 말맛을 자주 고정해야 할 때' },
  { turns: 3, label: '3턴마다 · 자주/안전', description: '중요 장면, 리그 초반, 관계 변화가 잦은 방' },
  { turns: 5, label: '5턴마다 · 기본 추천', description: '품질과 비용 균형이 가장 무난한 기본값' },
  { turns: 8, label: '8턴마다 · 비용 절약', description: '긴 흐름은 유지하되 압축 호출을 줄이고 싶을 때' },
  { turns: 12, label: '12턴마다 · 최소 압축', description: '테스트나 저비용 장시간 대화용' },
];
export const DEFAULT_RUNTIME_SETTING: RuntimeSettingDefaults = { model_key: null, fallback_model_key: null, compression_model_key: null, compression_fallback_model_key: null, effective_compression_fallback_model_key: null, compression_fallback_source: 'none', response_length_preset: 'medium', min_output_tokens: 768, compression_interval_turns: 5, options: [], compression_options: [], response_length_presets: [], compression_interval_options: FALLBACK_COMPRESSION_INTERVAL_OPTIONS };
export const FALLBACK_RESPONSE_LENGTH_PRESETS = [
  { key: 'short', label: '짧게', description: '비용 절약/빠른 티키타카. 보통 1~2버블.', target_output_tokens: 320 },
  { key: 'medium', label: '중간', description: '기본 역할놀이 밸런스. 감정선은 살리되 과출력 방지.', target_output_tokens: 768 },
  { key: 'long', label: '긴대화', description: '중요 장면/도장깨기용. 액션/속마음보다 버블을 더 나눠 4~6버블까지 유도.', target_output_tokens: 1280 },
];
export const USER_SETTING_PRESETS = [
  { title: '기본 사용자', content: '사용자는 캐릭터와 편하게 대화한다. 캐릭터가 설명적으로 굴기보다 자연스럽게 반응하길 선호한다.' },
  { title: '관찰자 모드', content: '사용자는 대화방의 관찰자이자 상황 조율자다. 캐릭터들의 감정과 관계 변화가 자연스럽게 드러나길 원한다.' },
  { title: '가까운 관계', content: '사용자는 캐릭터와 이미 어느 정도 친밀한 관계다. 딱딱한 설명보다 편한 말투와 자연스러운 리액션을 선호한다.' },
];
