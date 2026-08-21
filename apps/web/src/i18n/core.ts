import englishMessages from './messages.en.json';
import japaneseMessages from './messages.ja.json';
import koreanMessages from './messages.ko.json';

export const SUPPORTED_LOCALES = ['ko', 'en', 'ja'] as const;
export type AppLocale = (typeof SUPPORTED_LOCALES)[number];
export type UiMessageKey = keyof typeof englishMessages;
export type Translator = (key: UiMessageKey) => string;
export const LOCALE_STORAGE_KEY = 'lorechat-language';

export function formatUiCount(value: number, unit: 'items' | 'accounts' | 'people' | 'messages', locale: AppLocale): string {
  if (locale === 'en') {
    if (unit === 'people') return `${value} people`;
    if (unit === 'messages') return `${value} ${value === 1 ? 'message' : 'messages'}`;
    return `${value} ${unit}`;
  }
  if (locale === 'ja') {
    if (unit === 'accounts') return `アカウント ${value}件`;
    if (unit === 'people') return `${value}人`;
    if (unit === 'messages') return `メッセージ ${value}件`;
    return `${value}件`;
  }
  if (unit === 'accounts') return `계정 ${value}개`;
  if (unit === 'people') return `${value}명`;
  if (unit === 'messages') return `메시지 ${value}개`;
  return `${value}개`;
}

export function formatCharacterSlot(value: number, locale: AppLocale): string {
  if (locale === 'en') return `Character ${value}`;
  if (locale === 'ja') return `キャラクター ${value}`;
  return `${value}번 캐릭터`;
}

export function formatSelectedCharacters(value: number, locale: AppLocale): string {
  if (locale === 'en') return `${value} ${value === 1 ? 'character' : 'characters'} selected`;
  if (locale === 'ja') return `${value}人を選択済み`;
  return `${value}명 선택 완료`;
}

export function formatParticipantCount(value: number, locale: AppLocale): string {
  if (locale === 'en') return `${value} ${value === 1 ? 'character' : 'characters'}`;
  if (locale === 'ja') return `参加キャラクター ${value}人`;
  return `참여 캐릭터 ${value}명`;
}

export function formatUnreadCount(value: number, locale: AppLocale): string {
  if (locale === 'en') return `${value} unread ${value === 1 ? 'message' : 'messages'}`;
  if (locale === 'ja') return `未読メッセージ ${value}件`;
  return `안 읽은 메시지 ${value}개`;
}

export function formatNamedAction(name: string, action: 'open' | 'edit' | 'delete', locale: AppLocale): string {
  if (locale === 'en') {
    const verb = action === 'open' ? 'Open' : action === 'edit' ? 'Edit' : 'Delete';
    return `${verb} ${name}`;
  }
  if (locale === 'ja') {
    const suffix = action === 'open' ? 'を開く' : action === 'edit' ? 'を編集' : 'を削除';
    return `${name}${suffix}`;
  }
  const suffix = action === 'open' ? '열기' : action === 'edit' ? '편집' : '삭제';
  return `${name} ${suffix}`;
}

export function formatProfileImageAlt(name: string, locale: AppLocale): string {
  if (locale === 'en') return `${name} profile picture`;
  if (locale === 'ja') return `${name}のプロフィール画像`;
  return `${name} 프로필 사진`;
}

export function formatPagerSummary(page: number, pages: number, total: number, locale: AppLocale): string {
  if (locale === 'en') return `${page} of ${pages} · ${total} ${total === 1 ? 'item' : 'items'}`;
  if (locale === 'ja') return `${page} / ${pages}・全${total}件`;
  return `${page} / ${pages} · 총 ${total}개`;
}

export function formatRoomCount(value: number, locale: AppLocale): string {
  if (locale === 'en') return `${value} ${value === 1 ? 'chat' : 'chats'}`;
  if (locale === 'ja') return `チャット ${value}件`;
  return `대화방 ${value}개`;
}

export function formatCharacterActivity(messages: number, rooms: number, locale: AppLocale): string {
  if (locale === 'en') return `${messages} ${messages === 1 ? 'message' : 'messages'} · ${rooms} ${rooms === 1 ? 'chat' : 'chats'}`;
  if (locale === 'ja') return `メッセージ ${messages}件・チャット ${rooms}件`;
  return `메시지 ${messages}개 · 대화방 ${rooms}개`;
}

export function formatActiveMatch(first: string, second: string, locale: AppLocale): string {
  if (locale === 'en') return `Official match in progress: ${first} vs ${second}. End it before starting a new match.`;
  if (locale === 'ja') return `公式試合中：${first} vs ${second}。終了後に新しい試合を開始できます。`;
  return `공식 경기 진행 중: ${first} vs ${second}. 종료 후 새 경기를 시작할 수 있습니다.`;
}

export function formatCurrentMatch(first: string, second: string, locale: AppLocale): string {
  if (locale === 'en') return `Current match: ${first} vs ${second}`;
  if (locale === 'ja') return `現在の試合：${first} vs ${second}`;
  return `현재 경기: ${first} vs ${second}`;
}

export function formatBattleResult(winner: string, loser: string, locale: AppLocale): string {
  if (locale === 'en') return `${winner} won · ${loser} lost`;
  if (locale === 'ja') return `${winner} 勝・${loser} 敗`;
  return `${winner} 승 · ${loser} 패`;
}

export function formatBattleStanding(rank: string, name: string, wins: number, losses: number, points: number, locale: AppLocale): string {
  if (locale === 'en') return `#${rank} ${name} · ${wins}W ${losses}L · ${points} pts`;
  if (locale === 'ja') return `${rank}位 ${name}・${wins}勝 ${losses}敗・${points}pt`;
  return `${rank}위 ${name} · ${wins}승 ${losses}패 · ${points}점`;
}

export function formatStoryArcLimit(value: number, locale: AppLocale): string {
  if (locale === 'en') return `${value}/2,200 characters · Start each event on a new line with \`- \``;
  if (locale === 'ja') return `${value}/2,200文字・各イベントは \`- \` で改行`;
  return `${value}/2,200자 · 각 사건은 \`- \`로 한 줄씩`;
}

export function formatTextMetrics(characters: number, lines: number, locale: AppLocale): string {
  if (locale === 'en') return `${characters} characters · ${lines} lines`;
  if (locale === 'ja') return `${characters}文字・${lines}行`;
  return `${characters}자 · ${lines}줄`;
}

export function formatBattleControlSummary(activeNames: [string, string] | null, participantCount: number, locale: AppLocale): string {
  if (activeNames) {
    if (locale === 'en') return `In progress · ${activeNames[0]} vs ${activeNames[1]}`;
    if (locale === 'ja') return `進行中・${activeNames[0]} vs ${activeNames[1]}`;
    return `진행 중 · ${activeNames[0]} vs ${activeNames[1]}`;
  }
  if (participantCount > 0) {
    if (locale === 'en') return `Ready · ${participantCount} participants`;
    if (locale === 'ja') return `待機中・参加者${participantCount}人`;
    return `대기 · 참가자 ${participantCount}명`;
  }
  if (locale === 'en') return 'Status ready';
  if (locale === 'ja') return '状態を確認済み';
  return '상태 확인됨';
}

export type DangerConfirmationKind = 'memory' | 'removeCharacter' | 'character' | 'asset' | 'preset' | 'command' | 'world' | 'conversation' | 'messages' | 'message';

export function formatDangerConfirmation(kind: DangerConfirmationKind, locale: AppLocale, value?: string | number): string {
  const text = String(value ?? '');
  if (locale === 'en') {
    if (kind === 'memory') return 'Delete this note?';
    if (kind === 'removeCharacter') return `Remove ${text} from this chat?`;
    if (kind === 'character') return `Delete character “${text}”? Linked chats and assets may be affected.`;
    if (kind === 'asset') return 'Delete this asset? Linked message images may be affected.';
    if (kind === 'preset') return 'Delete this preset?';
    if (kind === 'command') return `Delete the ${text} command?`;
    if (kind === 'world') return `Delete ${text}? Existing chat snapshots will be kept.`;
    if (kind === 'conversation') return `Delete chat “${text}”? Its messages, relationships, and memories may also be deleted.`;
    if (kind === 'messages') return `Delete ${text} selected messages? They will be removed from the chat log.`;
    return 'Delete this message from the chat log?';
  }
  if (locale === 'ja') {
    if (kind === 'memory') return 'このメモを削除しますか？';
    if (kind === 'removeCharacter') return `${text}をこのチャットから退出させますか？`;
    if (kind === 'character') return `キャラクター「${text}」を削除しますか？ 関連するチャットやアセットに影響する場合があります。`;
    if (kind === 'asset') return 'このアセットを削除しますか？ メッセージに紐づく画像にも影響する場合があります。';
    if (kind === 'preset') return 'このプリセットを削除しますか？';
    if (kind === 'command') return `コマンド「${text}」を削除しますか？`;
    if (kind === 'world') return `${text}を削除しますか？ 既存チャットのスナップショットは保持されます。`;
    if (kind === 'conversation') return `チャット「${text}」を削除しますか？ メッセージ、関係、メモも削除される場合があります。`;
    if (kind === 'messages') return `選択した${text}件のメッセージを削除しますか？ チャット履歴から削除されます。`;
    return 'このメッセージをチャット履歴から削除しますか？';
  }
  if (kind === 'memory') return '이 유저노트를 삭제할까요?';
  if (kind === 'removeCharacter') return `${text} 캐릭터를 이 대화방에서 내보낼까요?`;
  if (kind === 'character') return `캐릭터 '${text}' 삭제할까요? 연결된 대화와 에셋에 영향을 줄 수 있습니다.`;
  if (kind === 'asset') return '이 에셋을 삭제할까요? 연결된 메시지 이미지에도 영향을 줄 수 있습니다.';
  if (kind === 'preset') return '이 프리셋을 삭제할까요?';
  if (kind === 'command') return `${text} 커맨드를 삭제할까요?`;
  if (kind === 'world') return `${text} 삭제할까요? 기존 대화방 스냅샷은 유지됩니다.`;
  if (kind === 'conversation') return `대화방 '${text}' 삭제할까요? 메시지, 관계, 메모도 함께 삭제될 수 있습니다.`;
  if (kind === 'messages') return `선택한 메시지 ${text}개를 삭제할까요? 대화 기록에서 삭제됩니다.`;
  return '이 메시지를 대화 기록에서 삭제할까요?';
}

const messages: Record<AppLocale, Partial<Record<UiMessageKey, string>>> = {
  ko: koreanMessages,
  en: englishMessages,
  ja: japaneseMessages,
};

export function isAppLocale(value: unknown): value is AppLocale {
  return typeof value === 'string' && SUPPORTED_LOCALES.includes(value as AppLocale);
}

export function initialLocale(): AppLocale {
  if (typeof localStorage === 'undefined') return 'ko';
  const stored = localStorage.getItem(LOCALE_STORAGE_KEY);
  return isAppLocale(stored) ? stored : 'ko';
}

export function translateUiText(key: UiMessageKey, locale: AppLocale): string {
  return messages[locale][key] || key;
}
