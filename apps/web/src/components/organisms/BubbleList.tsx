import { useRef, useState, type FormEvent, type RefObject, type UIEvent, type MouseEvent } from 'react';
import { Cloud } from 'lucide-react';
import { assetUrlFor, avatarUrlFor } from '../../utils/assets';
import { useUiStore } from '../../stores/uiStore';
import { Button, Input, Textarea } from '../atoms';
import { BubbleRichText, ChatImageFrame, CommandBlockRenderer, type CommandBlock } from '../molecules';
import { useI18n } from '../../i18n/I18nProvider';
import { formatProfileImageAlt } from '../../i18n/core';

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
  tts_audio_url?: string;
  tts_text?: string;
  failed?: boolean;
  error?: string;
  retryPayload?: unknown;
  metadata?: {
    command_blocks?: CommandBlock[];
    render_parts?: RenderPart[];
    [key: string]: unknown;
  };
};

type MessageEditDraft = { content: string; action: string; thought: string; emotion: string };

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

function renderMessagePart(part: RenderPart, key: string | number, side: string, thoughtLabel: string, isStorytelling = false) {
  const text = String(part.text || '');
  if (part.type === 'action') return <div
    className={`mb-2 rounded-r-xl border-l-2 px-2.5 py-1.5 text-[13px] italic leading-[1.45] ${side === 'right' ? 'border-white/35 bg-white/10 text-white/90' : 'border-primary/40 bg-primary/5 text-foreground/75'}`}
    data-semantic-part="action"
    key={key}
  ><BubbleRichText text={text} /></div>;
  if (part.type === 'thought') return <div
    aria-label={thoughtLabel}
    className={`mt-2 flex items-start gap-1.5 rounded-xl border px-2.5 py-2 text-[13px] italic leading-[1.45] ${side === 'right' ? 'border-white/20 bg-white/10 text-white/80' : 'border-violet-500/20 bg-violet-500/10 text-violet-950/75 dark:text-violet-100/80'}`}
    data-semantic-part="thought"
    key={key}
  ><Cloud aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 opacity-70" /><span className="min-w-0"><BubbleRichText text={text} /></span></div>;
  return <div
    className={`m-0 whitespace-pre-wrap text-[15px] font-[430] leading-[1.55] text-inherit ${isStorytelling ? 'italic' : ''}`}
    data-semantic-part="dialogue"
    key={key}
  ><BubbleRichText text={text} /></div>;
}

function messageTextPartsForCopy(message: ChatMessage): string[] {
  const renderParts = normalizedRenderPartsFor(message);
  if (renderParts.length) {
    const hasThoughtPart = renderParts.some((part) => part.type === 'thought');
    return [
      ...renderParts.map((part) => part.type === 'thought' ? `(${part.text})` : part.text || ''),
      ...(!hasThoughtPart && message.thought ? [`(${message.thought})`] : []),
    ].filter(Boolean);
  }
  return [message.action, message.content, message.thought && `(${message.thought})`].filter(Boolean) as string[];
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

export function BubbleList({ messages, characters, onAvatarPreview, bottomRef, threadRef, onLoadOlderMessages, hasOlderMessages = false, loadingOlderMessages = false, olderPagingReady = false, onRetryMessage, renderImages = true, onGenerateTts, onRegenerateMessage, onDeleteMessage, onUpdateMessage, busy = false, selectionMode = false, selectedMessageIds = [], onEnterSelectionMode, onToggleMessageSelection, onScrollAwayChange }: BubbleListProps) {
  const theme = useUiStore((state) => state.theme);
  const { locale, t } = useI18n();
  const [openMenuId, setOpenMenuId] = useState('');
  const [editingMessageId, setEditingMessageId] = useState('');
  const [editDraft, setEditDraft] = useState<MessageEditDraft>({ content: '', action: '', thought: '', emotion: '' });
  const longPressTimerRef = useRef<number | null>(null);
  const selectedIdSet = new Set(selectedMessageIds);
  const characterById = new Map(characters.map((c) => [c.id, c]));
  const nameById = new Map(characters.map((c) => [c.id, c.name]));
  const participantName = (message: ChatMessage) => {
    if (message.speaker_type === 'system') return t('시스템');
    if (message.speaker_type === 'storytelling') return t('스토리텔링');
    if (message.speaker_type === 'user') return t('나');
    if (message.is_typing) return nameById.get(message.speaker_id) || t('캐릭터');
    return nameById.get(message.speaker_id) || `${t('삭제된 캐릭터')} · ${String(message.speaker_id || '').slice(-4)}`;
  };
  const bubbleSide = (message: ChatMessage) => {
    if (message.speaker_type === 'system' || message.speaker_type === 'storytelling') return 'center';
    if (message.speaker_type === 'user') return 'right';
    return 'left';
  };
  const displayName = (message: ChatMessage) => {
    if (message.speaker_type === 'system') return t('시스템');
    if (message.speaker_type === 'storytelling') return t('스토리텔링');
    return participantName(message);
  };
  const characterForMessage = (message: ChatMessage) => characterById.get(message.speaker_id) || { id: message.speaker_id, name: displayName(message) };
  const copyMessage = async (message: ChatMessage) => {
    const blockText = commandBlocksFor(message).map((block) => {
      if (block.type === 'table') return [block.title, ...(block.rows || []).map((row) => row.join(' | '))].filter(Boolean).join('\n');
      if (block.type === 'comments' || block.type === 'commentary') return [block.title || t('LIVE 댓글'), ...(block.comments || []).map((item) => `${item.author || t('시청자')}: ${item.text || ''}`)].join('\n');
      return [block.title, block.text].filter(Boolean).join('\n');
    }).filter(Boolean).join('\n\n');
    const text = [...messageTextPartsForCopy(message), blockText].filter(Boolean).join('\n');
    await navigator.clipboard?.writeText(text || '');
    setOpenMenuId('');
  };
  const startMessageEdit = (message: ChatMessage) => {
    if (!message.id) return;
    setOpenMenuId('');
    setEditingMessageId(String(message.id));
    setEditDraft({
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
    if (!editDraft.content.trim() && !editDraft.action.trim() && !editDraft.thought.trim()) return;
    await onUpdateMessage?.(message, {
      content: editDraft.content.trim(),
      action: editDraft.action.trim(),
      thought: editDraft.thought.trim(),
      emotion: editDraft.emotion.trim(),
    });
    cancelMessageEdit();
  };
  const isSelectableMessage = (message: ChatMessage) => !!message?.id && !message.is_typing;
  const clearLongPressTimer = () => {
    if (longPressTimerRef.current) window.clearTimeout(longPressTimerRef.current);
    longPressTimerRef.current = null;
  };
  const startLongPress = (message: ChatMessage) => {
    if (!isSelectableMessage(message) || busy) return;
    clearLongPressTimer();
    longPressTimerRef.current = window.setTimeout(() => {
      setOpenMenuId('');
      onEnterSelectionMode?.(message.id);
    }, 520);
  };
  const handleBubbleClick = (event: MouseEvent<HTMLElement>, message: ChatMessage) => {
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

  return <div className="chat-thread" ref={threadRef} onScroll={handleThreadScroll} data-modernized="채팅 버블 shadcn primitive marker">
    {hasOlderMessages && <div className="grid place-items-center pb-2 pt-1"><Button type="button" variant="ghost" size="sm" className="min-h-[42px] px-3 py-2" disabled={loadingOlderMessages} onClick={onLoadOlderMessages}>{t(loadingOlderMessages ? '이전 대화 불러오는 중…' : '이전 대화 더 보기')}</Button></div>}
    {messages.map((message, index) => {
      const side = bubbleSide(message);
      const showAvatar = message.speaker_type === 'character' && side !== 'center';
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
      const renderPartsIncludeThought = renderParts.some((part) => part.type === 'thought');
      const showDialogue = !hasRenderParts && Boolean(message.content);
      const showAction = !hasRenderParts && Boolean(message.action);
      return <div className={`flex w-full items-end gap-2 ${side === 'right' ? 'justify-end' : ''} ${side === 'center' ? 'justify-center' : ''} ${selectionMode ? 'cursor-pointer' : ''}`} key={message.id || index}>
        {selectionMode && isSelectableMessage(message) && <Button type="button" variant="ghost" size="icon" className={`message-select-toggle ${selected ? 'selected' : ''}`} aria-label={t(selected ? '버블 선택 해제' : '버블 선택')} onClick={(event) => { event.stopPropagation(); onToggleMessageSelection?.(message.id); }}>{selected ? '✓' : ''}</Button>}
        {showAvatar && side === 'left' && <Button type="button" variant="ghost" size="icon" className="size-11 min-h-11 shrink-0 rounded-full border-0 bg-transparent p-0 shadow-none hover:bg-transparent" onClick={() => onAvatarPreview?.({ character, emotion: message.emotion || t('아직 없음'), src: avatarUrlFor(character, theme) })}><img className="size-9 rounded-full border border-white/20 bg-white/10 object-cover shadow-[0_6px_16px_rgba(0,0,0,.22)]" src={avatarUrlFor(character, theme)} alt={formatProfileImageAlt(String(character.name || t('캐릭터')), locale)} /></Button>}
        <article className={`bubble ${side} ${side === 'right' ? 'text-white' : ''} ${message.speaker_type === 'system' ? 'system' : ''} ${message.speaker_type === 'storytelling' ? 'storytelling' : ''} ${message.is_typing ? 'typing' : ''} ${selected ? 'selected' : ''}`} onPointerDown={() => startLongPress(message)} onPointerUp={clearLongPressTimer} onPointerCancel={clearLongPressTimer} onPointerLeave={clearLongPressTimer} onClick={(event) => handleBubbleClick(event, message)}>
          <div className={`bubble-name ${side === 'right' ? 'text-white/90' : ''}`}>{displayName(message)}</div>
          {message.is_typing ? <div className="inline-flex min-h-[22px] min-w-[52px] items-center gap-[5px]" aria-label={t('대화 생성 중')}><span className="size-[7px] animate-[typingPulse_1s_infinite_ease-in-out] rounded-full bg-current opacity-35"></span><span className="size-[7px] animate-[typingPulse_1s_infinite_ease-in-out] rounded-full bg-current opacity-35 [animation-delay:.14s]"></span><span className="size-[7px] animate-[typingPulse_1s_infinite_ease-in-out] rounded-full bg-current opacity-35 [animation-delay:.28s]"></span></div> : <>
            <div className="bubble-menu-wrap"><Button type="button" variant="ghost" size="icon" className="bubble-menu-button" aria-label={t('버블 액션 메뉴')} disabled={selectionMode} onClick={(event) => { event.stopPropagation(); setOpenMenuId(openMenuId === message.id ? '' : String(message.id || '')); }}>⋯</Button>{openMenuId === message.id && <div className="bubble-action-menu" role="menu"><Button type="button" variant="ghost" size="sm" onClick={() => copyMessage(message)}>{t('복사')}</Button><Button type="button" variant="ghost" size="sm" disabled={!canEdit || busy} onClick={() => startMessageEdit(message)}>{t('수정')}</Button>{message.speaker_type === 'character' && <Button type="button" variant="ghost" size="sm" onClick={() => { setOpenMenuId(''); onGenerateTts?.(message); }}>{t('TTS')}</Button>}{message.failed && message.retryPayload && <Button type="button" variant="ghost" size="sm" onClick={() => { setOpenMenuId(''); onRetryMessage?.(message); }}>{t('재시도')}</Button>}<Button type="button" variant="ghost" size="sm" disabled={!canRegenerate || busy} title={t(canRegenerate ? '이 메시지 이후를 새로 생성' : '저장된 캐릭터 메시지만 가능')} onClick={() => { setOpenMenuId(''); onRegenerateMessage?.(message); }}>{t('이후 재생성')}</Button><Button type="button" variant="destructive" size="sm" className="danger-menu-item" disabled={!canDelete || busy} title={t('이 버블 삭제')} onClick={() => { setOpenMenuId(''); onDeleteMessage?.(message); }}>{t('삭제')}</Button><Button type="button" variant="ghost" size="sm" disabled={!canDelete || busy} onClick={() => { setOpenMenuId(''); onEnterSelectionMode?.(message.id); }}>{t('여러 개 선택')}</Button></div>}</div>
            {isEditing ? <form className="grid min-w-[min(76vw,320px)] gap-2 rounded-2xl border border-border bg-card p-3 text-foreground" aria-label={t('버블 수정')} onSubmit={(event) => submitMessageEdit(event, message)} onPointerDown={(event) => event.stopPropagation()} onClick={(event) => event.stopPropagation()}>
              <label className="grid gap-1 text-xs font-semibold text-muted-foreground">{t('대사')}<Textarea rows={3} maxLength={8000} value={editDraft.content} onChange={(event) => setEditDraft({ ...editDraft, content: event.target.value })} aria-label={t('버블 대사 수정')} /></label>
              <label className="grid gap-1 text-xs font-semibold text-muted-foreground">{t('행동')}<Textarea rows={2} maxLength={4000} value={editDraft.action} onChange={(event) => setEditDraft({ ...editDraft, action: event.target.value })} aria-label={t('버블 행동 수정')} /></label>
              <label className="grid gap-1 text-xs font-semibold text-muted-foreground">{t('속마음')}<Textarea rows={2} maxLength={4000} value={editDraft.thought} onChange={(event) => setEditDraft({ ...editDraft, thought: event.target.value })} aria-label={t('버블 속마음 수정')} /></label>
              <label className="grid gap-1 text-xs font-semibold text-muted-foreground">{t('감정')}<Input maxLength={120} value={editDraft.emotion} onChange={(event) => setEditDraft({ ...editDraft, emotion: event.target.value })} aria-label={t('버블 감정 수정')} /></label>
              <div className="mt-1 grid grid-cols-2 gap-2"><Button type="button" variant="ghost" disabled={busy} onClick={cancelMessageEdit}>{t('취소')}</Button><Button type="submit" disabled={busy || (!editDraft.content.trim() && !editDraft.action.trim() && !editDraft.thought.trim())}>{t(busy ? '저장 중…' : '수정 저장')}</Button></div>
            </form> : <>
            {renderImages && message.assets && message.assets.length > 0 && <div className="bubble-assets grid gap-1.5">{message.assets.map((asset) => <ChatImageFrame key={asset.id} src={assetUrlFor(asset.thumbnail_url || asset.image_url)} alt={asset.label || t('상황별 이미지')} onClick={() => onAvatarPreview?.({ character: { id: asset.character_id, name: asset.label }, emotion: asset.description || t('이미지 에셋'), src: assetUrlFor(asset.image_url) })} />)}</div>}
            {hasRenderParts && renderParts.map((part, partIndex) => renderMessagePart(part, partIndex, side, t('속마음'), message.speaker_type === 'storytelling'))}
            {showAction && renderMessagePart({ type: 'action', text: message.action }, 'action', side, t('속마음'))}
            {showDialogue && renderMessagePart({ type: 'dialogue', text: message.content }, 'dialogue', side, t('속마음'), message.speaker_type === 'storytelling')}
            {hasCommandBlocks && <div className="mt-2.5 grid gap-2 rounded-2xl border border-border bg-card p-2.5 shadow-inner shadow-foreground/5">{commandBlocks.map((block, blockIndex) => <CommandBlockRenderer key={blockIndex} block={block} index={blockIndex} />)}</div>}
            {!hasRenderParts && !showDialogue && !showAction && commandBlocks.length === 0 && <p className="m-0 whitespace-pre-wrap text-[14px] font-[430] leading-[1.45] text-inherit">{t('(빈 응답)')}</p>}
            {message.thought && !renderPartsIncludeThought && renderMessagePart({ type: 'thought', text: message.thought }, 'thought', side, t('속마음'))}
            {message.speaker_type === 'character' && <div className="mt-2 flex justify-end gap-1.5 [&_audio]:h-8 [&_audio]:w-[min(240px,100%)] [&_button]:min-h-[30px] [&_button]:px-2.5 [&_button]:py-1.5 [&_button]:text-xs">{message.tts_audio_url ? <audio controls preload="none" src={assetUrlFor(message.tts_audio_url)} title={message.tts_text || t('TTS')} /> : <Button type="button" variant="ghost" size="sm" onClick={() => onGenerateTts?.(message)}>🔊 {t('TTS')}</Button>}</div>}
            </>}
            {message.failed && <div className="mt-2 text-[0.78rem] leading-[1.3] text-red-200">{t('전송 실패 ·')} {message.error || t('요청 실패')}</div>}
          </>}
          {message.failed && message.retryPayload && <div className="mt-[7px] flex justify-end"><Button type="button" variant="ghost" size="icon" className="size-11 min-h-11 rounded-full border-white/25 bg-white/15 p-0 text-[17px] text-inherit shadow-none hover:bg-white/20" aria-label={t('마지막 입력 재요청')} title={t('재요청')} onClick={() => onRetryMessage?.(message)}>↻</Button></div>}
        </article>
        {showAvatar && side === 'right' && <Button type="button" variant="ghost" size="icon" className="size-11 min-h-11 shrink-0 rounded-full border-0 bg-transparent p-0 shadow-none hover:bg-transparent" onClick={() => onAvatarPreview?.({ character, emotion: message.emotion || t('아직 없음'), src: avatarUrlFor(character, theme) })}><img className="size-9 rounded-full border border-white/20 bg-white/10 object-cover shadow-[0_6px_16px_rgba(0,0,0,.22)]" src={avatarUrlFor(character, theme)} alt={formatProfileImageAlt(String(character.name || t('캐릭터')), locale)} /></Button>}
      </div>;
    })}
    <div ref={bottomRef} className="w-full flex-[0_0_1px]" aria-hidden="true" />
  </div>;
}
