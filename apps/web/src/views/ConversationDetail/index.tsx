import { useEffect, type CSSProperties } from 'react';
import { BubbleList } from '../../components/organisms';
import { MessageSelectionToolbar } from '../../components/organisms/MessageSelectionToolbar';
import { Badge, Button } from '../../components/atoms';
import { useI18n } from '../../i18n/I18nProvider';
import { BattleControls } from './BattleControls';
import { ConversationComposer } from './ConversationComposer';
import type { ConversationDetailViewProps } from './contracts';
import { CONVERSATION_CHAT_MAX_WIDTH, CONVERSATION_PANEL_MIN_WIDTH } from '../../hooks/useConversationPanelLayout';
export type { ConversationDetailViewProps } from './contracts';

export function ConversationDetailView(props: ConversationDetailViewProps) {
  const {
    conversation, selectedConversationId, messageSelectionMode, selectedMessageIds,
    clearMessageSelection, busy, deleteSelectedMessages, messages, characters,
    participants, setAvatarPreview, chatEndRef, chatThreadRef, loadOlderMessages,
    hasOlderMessages, loadingOlderMessages, olderPagingReady, retryMessage,
    renderChatImages, generateMessageTts, regenerateFromMessage, deleteMessageBubble,
    updateMessageBubble, enterMessageSelectionMode, toggleMessageSelection,
    setShowJumpToLatest, showJumpToLatest, scrollToLatest, participantName,
  } = props;
  const { t } = useI18n();
  useEffect(() => {
    if (!messageSelectionMode) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) { event.preventDefault(); clearMessageSelection(); }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [messageSelectionMode, busy, clearMessageSelection]);

  return (
    <div ref={props.workspaceRef} className={`chat-workspace${props.panelDocked && props.panelOpen ? ' has-docked-panel' : ''}`} style={{ '--chat-max-width': `${CONVERSATION_CHAT_MAX_WIDTH}px`, '--chat-panel-width': `${props.panelWidth || CONVERSATION_PANEL_MIN_WIDTH}px` } as CSSProperties}>
    <section className="chat-shell page">
      <div className="chat-header-region"><ConversationHeader {...props} />{props.feedback}</div>
      <div className="chat-body-wrap">
        <BubbleList
          messages={messages}
          characters={characters}
          participants={participants}
          conversation={conversation}
          onAvatarPreview={setAvatarPreview}
          bottomRef={chatEndRef}
          threadRef={chatThreadRef}
          onLoadOlderMessages={loadOlderMessages}
          hasOlderMessages={hasOlderMessages}
          loadingOlderMessages={loadingOlderMessages}
          olderPagingReady={olderPagingReady}
          onRetryMessage={retryMessage}
          renderImages={renderChatImages}
          onGenerateTts={generateMessageTts}
          onRegenerateMessage={regenerateFromMessage}
          onDeleteMessage={deleteMessageBubble}
          onUpdateMessage={updateMessageBubble}
          busy={busy}
          selectionMode={messageSelectionMode}
          selectedMessageIds={selectedMessageIds}
          onEnterSelectionMode={enterMessageSelectionMode}
          onToggleMessageSelection={toggleMessageSelection}
          onScrollAwayChange={setShowJumpToLatest}
        />
        {showJumpToLatest && (
          <Button type="button" variant="ghost" className="jump-to-latest-button border border-rose-200 bg-white/95 text-rose-700 shadow-[0_10px_30px_rgba(244,63,94,.18)] hover:bg-rose-50 hover:text-rose-800 dark:border-slate-700 dark:bg-slate-900/95 dark:text-slate-100 dark:hover:bg-slate-800" onClick={scrollToLatest}>
            {t('현재 대화로 ↓')}
          </Button>
        )}
      </div>
      <div className="chat-footer">
      <BattleControls {...props} visible={!messageSelectionMode} />
      {messageSelectionMode ? (
        <MessageSelectionToolbar
          key={selectedConversationId}
          messages={messages}
          selectedMessageIds={selectedMessageIds}
          participantName={participantName}
          busy={busy}
          onCancel={clearMessageSelection}
          onDelete={deleteSelectedMessages}
        />
      ) : <ConversationComposer {...props} />}
      </div>
    </section>
    {props.sidePanel}
    </div>
  );
}

type ConversationHeaderProps = Pick<ConversationDetailViewProps,
  'conversation' | 'selectedConversationId' | 'status' | 'navigate' |
  'openConversationContextDrawer' | 'panelOpen' | 'chatCommands' | 'clearActiveCommand'>;

function ConversationHeader({ conversation, selectedConversationId, status, navigate, openConversationContextDrawer, panelOpen, chatCommands = [], clearActiveCommand }: ConversationHeaderProps) {
  const { t } = useI18n();
  const activeCommandIds = Array.isArray(conversation?.active_command_ids) && conversation.active_command_ids.length
    ? conversation.active_command_ids
    : conversation?.active_command_id ? [conversation.active_command_id] : [];
  const activeCommands = activeCommandIds.map((commandId) => chatCommands.find((command) => command.id && command.id === commandId) || { id: commandId, name: t('커맨드'), display_name: t('커맨드') });
  return (
    <header className="chat-header">
      <Button type="button" variant="ghost" size="icon" className="chat-header-button chat-back-button" aria-label={t('대화방 목록으로')} onClick={() => navigate('/conversations')}>‹</Button>
      <div className="chat-title-block"><h2>{conversation?.title || selectedConversationId}</h2><p>{status}</p></div>
      <div className="chat-header-actions">
        {activeCommands.map((command) => {
          const label = command.display_name || command.name || t('커맨드');
          return (
            <Badge className="active-command-badge" title={t('활성 커맨드')} key={command.id || label}>
              <span>{label}</span>
              <button type="button" className="active-command-close" aria-label={`${label} ${t('커맨드 해제')}`} onClick={() => clearActiveCommand?.(command.id)}>×</button>
            </Badge>
          );
        })}
        <Button type="button" variant="ghost" size="icon" className="chat-header-button chat-context-button" aria-label={t(panelOpen ? '대화 정보 닫기' : '대화 흐름 열기')} aria-expanded={panelOpen} onClick={openConversationContextDrawer}>☰</Button>
      </div>
    </header>
  );
}
