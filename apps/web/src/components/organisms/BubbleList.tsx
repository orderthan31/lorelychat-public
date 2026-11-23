import { WebpImage } from '../atoms/WebpImage';
import { Component, type ReactNode, memo, useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent, type RefObject, type UIEvent, type MouseEvent, type PointerEvent } from 'react';
import { useChatWindow } from '../../hooks/useChatWindow';
import { Cloud } from 'lucide-react';
import { assetUrlFor, avatarUrlFor, imageUrlFor } from '../../utils/assets';
import { useUiStore } from '../../stores/uiStore';
import { Button, Input, Textarea } from '../atoms';
import { BubbleRichText, ChatImageFrame, CommandBlockRenderer, type CommandBlock } from '../molecules';
import { useI18n } from '../../i18n/I18nProvider';
import type { MessageEditDraft } from '../../types/domain';
import { formatProfileImageAlt } from '../../i18n/core';
import { messageTextForCopy, writeMessageTextToClipboard } from '../../utils/messageClipboard';

class BubbleContentBoundary extends Component<{ children: ReactNode; source: ChatMessage; failureLabel: string; retryLabel: string }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (!this.state.failed) return this.props.children;
    return <section role="alert" data-bubble-fallback><p>{this.props.failureLabel}</p><pre className="whitespace-pre-wrap break-all">{typeof this.props.source.content === 'string' ? this.props.source.content : JSON.stringify(this.props.source.content)}</pre><Button type="button" className="min-h-11" onClick={() => this.setState({ failed: false })}>{this.props.retryLabel}</Button></section>;
  }
}

type Character = { id?: string; name?: string; [key: string]: unknown };
type Participant = { id?: string; type?: string; [key: string]: unknown };
type Conversation = { id?: string; title?: string; [key: string]: unknown };
export type MessageAsset = { id?: string; character_id?: string; label?: string; description?: string; image_url?: string; thumbnail_url?: string };
export type RenderPart = { type?: string; text?: string };
export type ChatMessage = {
  id?: string;
  speaker_type?: string;
  speaker_id?: string;
  is_typing?: boolean;
  action?: string;
  content?: string;
  thought?: string;
  emotion?: string;
  assets?: MessageAsset[];
  asset_decision_pending?: boolean;
  tts_audio_url?: string;
  tts_text?: string;
  failed?: boolean;
  error?: string;
  retryPayload?: unknown;
  metadata?: {
    command_blocks?: CommandBlock[];
    render_parts?: RenderPart[];
    response_protocol?: 'tagged_text' | 'free_body_json';
    raw_body?: string;
    [key: string]: unknown;
  };
};

function isFreeBody(message: ChatMessage) {
  return ['tagged_text', 'free_body_json'].includes(message.metadata?.response_protocol || '');
}

function hasEditText(draft: MessageEditDraft) {
  return draft.raw_body !== undefined ? Boolean(draft.raw_body.trim()) : Boolean(draft.content?.trim() || draft.action?.trim() || draft.thought?.trim());
}

type AvatarPreviewPayload = { character: Character; emotion?: string; src?: string };

function commandBlocksFor(message: ChatMessage): CommandBlock[] {
  return Array.isArray(message.metadata?.command_blocks) ? message.metadata.command_blocks : [];
}

function normalizedRenderPartsFor(message: ChatMessage): RenderPart[] {
  const rawParts = Array.isArray(message.metadata?.render_parts) ? message.metadata?.render_parts : [];
  return rawParts
    .map((part) => ({ type: String(part?.type || '').toLowerCase(), text: String(part?.text || '').trim() }))
    .filter((part) => ['dialogue', 'action', 'thought'].includes(part.type) && part.text);
}

function renderMessagePart(part: RenderPart, key: string | number, thoughtLabel: string, isStorytelling = false, literalHtml = false) {
  const text = String(part.text || '');
  if (part.type === 'thought') return <div className="scene-thought" key={key}>
    <span className="scene-thought-label"><Cloud aria-hidden="true" className="size-3.5 shrink-0" />{thoughtLabel}</span>
    <div data-semantic-part="thought"><BubbleRichText text={text} literalHtml={literalHtml} /></div>
  </div>;
  return <div key={key} className="scene-prose" data-semantic-part={part.type} data-narration={isStorytelling || undefined}>
    <BubbleRichText text={text} literalHtml={literalHtml} />
  </div>;
}

export type BubbleListProps = {
  messages: ChatMessage[];
  characters: Character[];
  participants?: Participant[];
  conversation?: Conversation | null;
  onAvatarPreview?: (payload: AvatarPreviewPayload) => void;
  bottomRef: RefObject<HTMLDivElement>;
  threadRef: RefObject<HTMLDivElement>;
  onLoadOlderMessages?: () => void;
  hasOlderMessages?: boolean;
  loadingOlderMessages?: boolean;
  olderPagingReady?: boolean;
  onRetryMessage?: (message: ChatMessage) => void;
  renderImages?: boolean;
  onGenerateTts?: (message: ChatMessage) => void;
  onRegenerateMessage?: (message: ChatMessage) => void;
  onDeleteMessage?: (message: ChatMessage) => void;
  onUpdateMessage?: (message: ChatMessage, draft: MessageEditDraft) => Promise<unknown> | unknown;
  busy?: boolean;
  selectionMode?: boolean;
  selectedMessageIds?: string[];
  onEnterSelectionMode?: (messageId?: string) => void;
  onToggleMessageSelection?: (messageId?: string) => void;
  onScrollAwayChange?: (away: boolean) => void;
};

const CALLBACK_KEYS = ['onAvatarPreview', 'onLoadOlderMessages', 'onRetryMessage', 'onGenerateTts', 'onRegenerateMessage', 'onDeleteMessage', 'onUpdateMessage', 'onEnterSelectionMode', 'onToggleMessageSelection', 'onScrollAwayChange'] as const;

// Parent composer keystrokes must not traverse/reparse the history. Stable
// forwarding callbacks retain fresh room closures without ignoring prop changes.
export function BubbleList({ messages, characters, onAvatarPreview, bottomRef, threadRef, onLoadOlderMessages, hasOlderMessages = false, loadingOlderMessages = false, olderPagingReady = false, onRetryMessage, renderImages = true, onGenerateTts, onRegenerateMessage, onDeleteMessage, onUpdateMessage, busy = false, selectionMode = false, ...rest }: BubbleListProps) {
  const props: BubbleListProps = { messages, characters, onAvatarPreview, bottomRef, threadRef, onLoadOlderMessages, hasOlderMessages, loadingOlderMessages, olderPagingReady, onRetryMessage, renderImages, onGenerateTts, onRegenerateMessage, onDeleteMessage, onUpdateMessage, busy, selectionMode, ...rest };
  const live = useRef(props);
  useLayoutEffect(() => { live.current = props; });
  const callbacks = useMemo(() => Object.fromEntries(CALLBACK_KEYS.map((key) => [key, (...args: unknown[]) => (live.current[key] as Function | undefined)?.(...args)])), []);
  const forwarded = { ...props };
  for (const key of CALLBACK_KEYS) (forwarded as any)[key] = props[key] ? callbacks[key] : undefined;
  return <MemoBubbleList key={props.conversation?.id || ''} {...forwarded} />;
}

const MemoBubbleList = memo(BubbleListContent);
function BubbleListContent({ messages, characters, onAvatarPreview, bottomRef, threadRef, onLoadOlderMessages, hasOlderMessages = false, loadingOlderMessages = false, olderPagingReady = false, onRetryMessage, renderImages = true, onGenerateTts, onRegenerateMessage, onDeleteMessage, onUpdateMessage, busy = false, selectionMode = false, selectedMessageIds = [], onEnterSelectionMode, onToggleMessageSelection, onScrollAwayChange }: BubbleListProps) {
  const windowed = useChatWindow(messages, threadRef);
  const theme = useUiStore((state) => state.theme);
  const { locale, t } = useI18n();
  const [openMenuId, setOpenMenuId] = useState('');
  const [copyFeedback, setCopyFeedback] = useState<'복사 완료' | '복사 실패' | null>(null);
  const copyVersion = useRef(0);
  useEffect(() => () => { copyVersion.current += 1; }, []);
  const [editingMessageId, setEditingMessageId] = useState('');
  const [editError, setEditError] = useState('');
  const [editDraft, setEditDraft] = useState<MessageEditDraft>({ content: '', action: '', thought: '', emotion: '' });
  const longPressTimerRef = useRef<number | null>(null);
  const longPressOriginRef = useRef<{ x: number; y: number } | null>(null);
  const suppressBubbleClickRef = useRef<string | undefined>(undefined);
  useEffect(() => () => { if (longPressTimerRef.current) window.clearTimeout(longPressTimerRef.current); }, []);
  const selectedIdSet = useMemo(() => new Set(selectedMessageIds), [selectedMessageIds]);
  const characterById = useMemo(() => new Map(characters.map((c) => [c.id, c])), [characters]);
  const nameById = useMemo(() => new Map(characters.map((c) => [c.id, c.name])), [characters]);
  const participantName = (message: ChatMessage) => {
    if (message.speaker_type === 'system') return t('시스템');
    if (message.speaker_type === 'storytelling') return t('스토리텔링');
    if (message.speaker_type === 'user') return t('나');
    if (message.is_typing) return nameById.get(message.speaker_id) || t('캐릭터');
    return nameById.get(message.speaker_id) || `${t('삭제된 캐릭터')} · ${String(message.speaker_id || '').slice(-4)}`;
  };
  const displayName = (message: ChatMessage) => {
    if (message.speaker_type === 'system') return t('시스템');
    if (message.speaker_type === 'storytelling') return t('스토리텔링');
    return participantName(message);
  };
  const characterForMessage = (message: ChatMessage) => characterById.get(message.speaker_id) || { id: message.speaker_id, name: displayName(message) };
  const copyMessage = async (message: ChatMessage) => {
    const version = ++copyVersion.current;
    setCopyFeedback(null);
    setOpenMenuId('');
    try {
      await writeMessageTextToClipboard(messageTextForCopy(message, t));
      if (version === copyVersion.current) setCopyFeedback('복사 완료');
    } catch {
      if (version === copyVersion.current) setCopyFeedback('복사 실패');
    }
  };
  const startMessageEdit = (message: ChatMessage) => {
    if (!message.id) return;
    setOpenMenuId('');
    setEditError('');
    setEditingMessageId(String(message.id));
    setEditDraft(isFreeBody(message) && typeof message.metadata?.raw_body === 'string' ? {
      raw_body: message.metadata.raw_body,
      emotion: message.emotion || '',
    } : {
      content: message.content || '',
      action: message.action || '',
      thought: message.thought || '',
      emotion: message.emotion || '',
    });
  };
  const cancelMessageEdit = () => {
    setEditingMessageId('');
    setEditDraft({ content: '', action: '', thought: '', emotion: '' });
  };
  const submitMessageEdit = async (event: FormEvent<HTMLFormElement>, message: ChatMessage) => {
    event.preventDefault();
    if (!hasEditText(editDraft)) return;
    setEditError('');
    try {
    await onUpdateMessage?.(message, editDraft.raw_body !== undefined ? {
      raw_body: editDraft.raw_body,
      emotion: editDraft.emotion.trim(),
    } : {
      content: editDraft.content?.trim() || '',
      action: editDraft.action?.trim() || '',
      thought: editDraft.thought?.trim() || '',
      emotion: editDraft.emotion.trim(),
    });
    cancelMessageEdit();
    } catch (error) { setEditError(error instanceof Error ? error.message : String(error)); }
  };
  const isSelectableMessage = (message: ChatMessage) => !!message?.id && !message.is_typing;
  const clearLongPressTimer = () => {
    if (longPressTimerRef.current) window.clearTimeout(longPressTimerRef.current);
    longPressTimerRef.current = null;
    longPressOriginRef.current = null;
  };
  const startLongPress = (message: ChatMessage, event: PointerEvent<HTMLElement>) => {
    suppressBubbleClickRef.current = undefined;
    if (event.button !== 0 || (event.target instanceof Element && event.target.closest('button, a, input, textarea, select, audio, video')) || !isSelectableMessage(message) || busy || selectionMode) return;
    clearLongPressTimer();
    longPressOriginRef.current = { x: event.clientX, y: event.clientY };
    longPressTimerRef.current = window.setTimeout(() => {
      setOpenMenuId('');
      suppressBubbleClickRef.current = message.id;
      longPressTimerRef.current = null;
      onEnterSelectionMode?.(message.id);
    }, 520);
  };
  const handleBubbleClick = (event: MouseEvent<HTMLElement>, message: ChatMessage) => {
    if (suppressBubbleClickRef.current === message.id && message.id) {
      suppressBubbleClickRef.current = undefined;
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    if (!selectionMode || !isSelectableMessage(message)) return;
    event.preventDefault();
    event.stopPropagation();
    onToggleMessageSelection?.(message.id);
  };
  const handleThreadScroll = (event: UIEvent<HTMLDivElement>) => {
    const node = event.currentTarget;
    const isAwayFromBottom = node.scrollHeight - node.scrollTop - node.clientHeight > 140;
    onScrollAwayChange?.(isAwayFromBottom);
    if (olderPagingReady && node.scrollTop < 80 && hasOlderMessages && !loadingOlderMessages) onLoadOlderMessages?.();
  };

  return <div className="chat-thread" style={{ overflowAnchor: 'none' }} tabIndex={0} ref={threadRef} onScroll={handleThreadScroll} data-modernized="채팅 버블 design-system primitive marker">
    <div role="status" aria-live="polite" aria-atomic="true" className={copyFeedback ? 'sticky top-0 z-30 shrink-0 rounded-lg border border-border bg-card px-3 py-2 text-sm text-foreground' : 'sr-only'}>{copyFeedback ? t(copyFeedback) : ''}</div>
    {hasOlderMessages && <div className="grid place-items-center pb-2 pt-1"><Button type="button" variant="ghost" size="sm" className="min-h-[42px] px-3 py-2" disabled={loadingOlderMessages} onClick={onLoadOlderMessages}>{t(loadingOlderMessages ? '이전 대화 불러오는 중…' : '이전 대화 더 보기')}</Button></div>}
    <div ref={windowed.listRef} role="list" data-chat-window data-total-messages={messages.length} style={{ position: 'relative', height: windowed.total, flexShrink: 0 }}>
    <div style={{ position: 'absolute', top: windowed.top, left: 0, right: 0, display: 'flex', flexDirection: 'column', gap: 7 }}>
    {messages.slice(windowed.start, windowed.end).map((message, index) => {
      const showAvatar = message.speaker_type === 'character';
      const character = characterForMessage(message);
      const isPersistedMessage = message.id && !String(message.id).startsWith('local_') && !message.is_typing;
      const canRegenerate = message.speaker_type === 'character' && isPersistedMessage;
      const canEdit = Boolean(isPersistedMessage && onUpdateMessage);
      const canDelete = message.id && !message.is_typing;
      const isEditing = editingMessageId === String(message.id || '');
      const selected = selectedIdSet.has(String(message.id || ''));
      const commandBlocks = commandBlocksFor(message);
      const hasCommandBlocks = commandBlocks.length > 0;
      const renderParts = normalizedRenderPartsFor(message);
      const hasRenderParts = renderParts.length > 0;
      const freeBody = isFreeBody(message);
      const canSpeak = message.speaker_type === 'character' && (hasRenderParts ? renderParts.some((part) => part.type === 'dialogue') : !freeBody && Boolean(typeof message.content === 'string' && message.content.trim()));
      const renderPartsIncludeThought = renderParts.some((part) => part.type === 'thought');
      const showDialogue = !hasRenderParts && Boolean(message.content);
      const showAction = !hasRenderParts && Boolean(message.action);
      return <div data-chat-row={message.id || `row_${windowed.start + index}`} role="listitem" aria-posinset={windowed.start + index + 1} aria-setsize={messages.length} className={`scene-row ${selectionMode ? 'cursor-pointer' : ''}`} key={message.id || index}>
        {selectionMode && isSelectableMessage(message) && <Button type="button" variant="ghost" size="icon" className={`message-select-toggle ${selected ? 'selected' : ''}`} aria-label={t(selected ? '버블 선택 해제' : '버블 선택')} onClick={(event) => { event.stopPropagation(); onToggleMessageSelection?.(message.id); }}>{selected ? '✓' : ''}</Button>}
        <article className={`bubble scene-message ${selected ? 'selected' : ''}`} data-speaker={message.speaker_type} onPointerDown={(event) => startLongPress(message, event)} onPointerMove={(event) => { const origin = longPressOriginRef.current; if (origin && Math.hypot(event.clientX - origin.x, event.clientY - origin.y) > 10) clearLongPressTimer(); }} onPointerUp={clearLongPressTimer} onPointerCancel={clearLongPressTimer} onPointerLeave={clearLongPressTimer} onClick={(event) => handleBubbleClick(event, message)}>
          <header className="scene-speaker">
            {showAvatar && <Button type="button" variant="ghost" size="icon" className="scene-avatar" onClick={() => onAvatarPreview?.({ character, emotion: message.emotion || t('아직 없음'), src: avatarUrlFor(character, theme, 'full') })}><WebpImage className="size-7 rounded-full bg-muted object-cover" src={avatarUrlFor(character, theme)} alt={formatProfileImageAlt(String(character.name || t('캐릭터')), locale)} /></Button>}
            <div className="bubble-name">{displayName(message)}</div>
            {!message.is_typing && <div className="bubble-menu-wrap"><Button type="button" variant="ghost" size="icon" className="bubble-menu-button" aria-label={t('버블 액션 메뉴')} disabled={selectionMode} onClick={(event) => { event.stopPropagation(); copyVersion.current += 1; setCopyFeedback(null); setOpenMenuId(openMenuId === message.id ? '' : String(message.id || '')); }}>⋯</Button>{openMenuId === message.id && <div className="bubble-action-menu" role="menu"><Button type="button" variant="ghost" size="sm" onClick={() => copyMessage(message)}>{t('복사')}</Button><Button type="button" variant="ghost" size="sm" disabled={!canEdit || busy} onClick={() => startMessageEdit(message)}>{t('수정')}</Button>{canSpeak && <Button type="button" variant="ghost" size="sm" onClick={() => { setOpenMenuId(''); onGenerateTts?.(message); }}>{t('TTS')}</Button>}{message.failed && message.retryPayload && <Button type="button" variant="ghost" size="sm" onClick={() => { setOpenMenuId(''); onRetryMessage?.(message); }}>{t('재시도')}</Button>}<Button type="button" variant="ghost" size="sm" disabled={!canRegenerate || busy} title={t(canRegenerate ? '이 메시지 이후를 새로 생성' : '저장된 캐릭터 메시지만 가능')} onClick={() => { setOpenMenuId(''); onRegenerateMessage?.(message); }}>{t('이후 재생성')}</Button><Button type="button" variant="destructive" size="sm" className="danger-menu-item" disabled={!canDelete || busy} title={t('이 버블 삭제')} onClick={() => { setOpenMenuId(''); onDeleteMessage?.(message); }}>{t('삭제')}</Button><Button type="button" variant="ghost" size="sm" disabled={!canDelete || busy} onClick={() => { setOpenMenuId(''); onEnterSelectionMode?.(message.id); }}>{t('여러 개 선택')}</Button></div>}</div>}
          </header>
          {message.is_typing ? <div className="inline-flex min-h-[22px] min-w-[52px] items-center gap-[5px]" aria-label={t('대화 생성 중')}><span className="size-[7px] animate-[typingPulse_1s_infinite_ease-in-out] rounded-full bg-current opacity-35"></span><span className="size-[7px] animate-[typingPulse_1s_infinite_ease-in-out] rounded-full bg-current opacity-35 [animation-delay:.14s]"></span><span className="size-[7px] animate-[typingPulse_1s_infinite_ease-in-out] rounded-full bg-current opacity-35 [animation-delay:.28s]"></span></div> : <>
            {isEditing ? <form className="grid w-full min-w-0 grid-cols-1 gap-2 rounded-2xl border border-border bg-card p-3 text-foreground" aria-label={t('버블 수정')} onSubmit={(event) => submitMessageEdit(event, message)} onPointerDown={(event) => event.stopPropagation()} onClick={(event) => event.stopPropagation()}>
              {editError && <p role="alert">{editError}</p>}
              {editDraft.raw_body !== undefined ? <label className="grid min-w-0 gap-1 text-xs font-semibold text-muted-foreground">{t('본문')}<Textarea rows={7} value={editDraft.raw_body} onChange={(event) => setEditDraft({ ...editDraft, raw_body: event.target.value })} aria-label={t('버블 본문 수정')} /></label> : <>
              <label className="grid min-w-0 grid-cols-1 gap-1 text-xs font-semibold text-muted-foreground">{t('대사')}<Textarea rows={3} maxLength={8000} value={editDraft.content} onChange={(event) => setEditDraft({ ...editDraft, content: event.target.value })} aria-label={t('버블 대사 수정')} /></label>
              <label className="grid min-w-0 grid-cols-1 gap-1 text-xs font-semibold text-muted-foreground">{t('행동')}<Textarea rows={2} maxLength={4000} value={editDraft.action} onChange={(event) => setEditDraft({ ...editDraft, action: event.target.value })} aria-label={t('버블 행동 수정')} /></label>
              <label className="grid min-w-0 grid-cols-1 gap-1 text-xs font-semibold text-muted-foreground">{t('속마음')}<Textarea rows={2} maxLength={4000} value={editDraft.thought} onChange={(event) => setEditDraft({ ...editDraft, thought: event.target.value })} aria-label={t('버블 속마음 수정')} /></label></>}
              <label className="grid min-w-0 grid-cols-1 gap-1 text-xs font-semibold text-muted-foreground">{t('감정')}<Input maxLength={120} value={editDraft.emotion} onChange={(event) => setEditDraft({ ...editDraft, emotion: event.target.value })} aria-label={t('버블 감정 수정')} /></label>
              <div className="mt-1 grid grid-cols-2 gap-2"><Button type="button" variant="ghost" disabled={busy} onClick={cancelMessageEdit}>{t('취소')}</Button><Button type="submit" disabled={busy || !hasEditText(editDraft)}>{t(busy ? '저장 중…' : '수정 저장')}</Button></div>
            </form> : <>
            <BubbleContentBoundary key={`${message.id}:${message.content}`} source={message} failureLabel={t('표시할 수 없는 콘텐츠')} retryLabel={t('재시도')}>
            {renderImages && message.assets && message.assets.length > 0 && <div className="bubble-assets grid gap-1.5">{(Array.isArray(message.assets) ? message.assets : []).filter(asset => asset && typeof asset === 'object' && typeof (asset.thumbnail_url || asset.image_url) === 'string').map((asset) => <ChatImageFrame key={asset.id} src={imageUrlFor(asset.thumbnail_url || asset.image_url)} alt={asset.label || t('상황별 이미지')} onClick={() => onAvatarPreview?.({ character: { id: asset.character_id, name: asset.label }, emotion: asset.description || t('이미지 에셋'), src: imageUrlFor(asset.image_url, 'full') })} />)}</div>}
            {hasRenderParts && renderParts.map((part, partIndex) => renderMessagePart(part, partIndex, t('속마음'), message.speaker_type === 'storytelling', freeBody))}
            {showAction && renderMessagePart({ type: 'action', text: message.action }, 'action', t('속마음'))}
            {showDialogue && renderMessagePart({ type: 'dialogue', text: message.content }, 'dialogue', t('속마음'), message.speaker_type === 'storytelling')}
            {hasCommandBlocks && <div className="mt-3 grid min-w-0 gap-2">{commandBlocks.map((block, blockIndex) => <CommandBlockRenderer key={blockIndex} block={block} index={blockIndex} />)}</div>}
            {!hasRenderParts && !showDialogue && !showAction && commandBlocks.length === 0 && <p className="m-0 whitespace-pre-wrap text-[14px] font-[430] leading-[1.45] text-inherit">{t('(빈 응답)')}</p>}
            {message.thought && !freeBody && !renderPartsIncludeThought && renderMessagePart({ type: 'thought', text: message.thought }, 'thought', t('속마음'))}
            {canSpeak && <div className="mt-2 flex items-center gap-1.5 [&_audio]:h-8 [&_audio]:w-[min(240px,100%)] [&_button]:min-h-11 [&_button]:px-2.5 [&_button]:py-1.5 [&_button]:text-xs">{message.tts_audio_url ? <audio controls preload="none" src={assetUrlFor(message.tts_audio_url)} title={message.tts_text || t('TTS')} /> : <Button type="button" variant="ghost" size="sm" onClick={() => onGenerateTts?.(message)}>🔊 {t('TTS')}</Button>}</div>}
            </BubbleContentBoundary>
            </>}
            {message.failed && <div className="mt-2 text-[0.78rem] leading-[1.3] text-destructive">{t('전송 실패 ·')} {message.error || t('요청 실패')}</div>}
          </>}
          {message.failed && message.retryPayload && <div className="mt-[7px] flex justify-end"><Button type="button" variant="ghost" size="icon" className="size-11 min-h-11 p-0 text-[17px] text-inherit" aria-label={t('마지막 입력 재요청')} title={t('재요청')} onClick={() => onRetryMessage?.(message)}>↻</Button></div>}
        </article>
      </div>;
    })}
    </div>
    </div>
    <div ref={bottomRef} className="w-full flex-[0_0_1px]" aria-hidden="true" />
  </div>;
}
