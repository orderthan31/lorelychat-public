import { useEffect, useState } from 'react';
import { BubbleList } from '../../components/organisms';
import { ChatSettingsBubble } from '../../components/organisms';
import { Badge } from '../../components/atoms';
import { Button } from '../../components/atoms';
import { Card } from '../../components/atoms';
import { Input } from '../../components/atoms';
import { Select } from '../../components/atoms';
import { Slider } from '../../components/atoms';
import { Textarea } from '../../components/atoms';
import { useI18n } from '../../i18n/I18nProvider';
import { formatActiveMatch, formatBattleControlSummary, formatCurrentMatch } from '../../i18n/core';

export type ConversationDetailViewProps = Record<string, any>;

const DEFAULT_BATTLE_CONTROL_DRAFT = { action: 'none', advantage: 0.5, progress_balance: 0.5, current_phase: 'opening' };

export function ConversationDetailView({
  conversation,
  selectedConversationId,
  status,
  navigate,
  openConversationContextDrawer,
  messageSelectionMode,
  selectedMessageIds,
  clearMessageSelection,
  busy,
  deleteSelectedMessages,
  messages,
  characters,
  participants,
  setAvatarPreview,
  chatEndRef,
  chatThreadRef,
  loadOlderMessages,
  hasOlderMessages,
  loadingOlderMessages,
  olderPagingReady,
  retryMessage,
  renderChatImages,
  generateMessageTts,
  regenerateFromMessage,
  deleteMessageBubble,
  updateMessageBubble,
  enterMessageSelectionMode,
  toggleMessageSelection,
  setShowJumpToLatest,
  showJumpToLatest,
  scrollToLatest,
  sendConversationMessage,
  speakerId,
  setSpeakerId,
  speakerOptions,
  participantName,
  setChatSettingsOpen,
  chatSettingsOpen,
  conversationRuntimeSetting,
  setConversationRuntimeSetting,
  saveConversationRuntimeSetting,
  setRenderChatImages,
  setConversation,
  compressConversationNow,
  composerTextareaRef,
  compose,
  setCompose,
  resizeComposerTextarea,
  handleComposerKeyDown,
  composeExpanded,
  setComposeExpanded,
  battleState,
  battleControlDraft,
  setBattleControlDraft,
  chatCommands = [],
  clearActiveCommand
}: ConversationDetailViewProps) {
  const { locale, t } = useI18n();
  const battleParticipants = battleState?.participants || [];
  const activeMatch = battleState?.active_match;
  const battleFighters = activeMatch ? battleParticipants.filter((item) => [activeMatch.participant_a_id, activeMatch.participant_b_id].includes(item.id)) : battleParticipants;
  const battleAction = battleControlDraft?.action || 'none';
  const [battleControlOpen, setBattleControlOpen] = useState(false);
  const battleControlSummary = formatBattleControlSummary(activeMatch ? [activeMatch.participant_a_name, activeMatch.participant_b_name] : null, battleParticipants.length, locale);
  const activeCommandIds = Array.isArray(conversation?.active_command_ids) && conversation.active_command_ids.length
    ? conversation.active_command_ids
    : conversation?.active_command_id ? [conversation.active_command_id] : [];
  const activeCommands = activeCommandIds.map((commandId) => chatCommands.find((command) => command.id && command.id === commandId) || { id: commandId, name: t('커맨드'), display_name: t('커맨드') });
  const enabledChatCommands = chatCommands.filter((command) => command.enabled !== false);
  const commandBody = compose.startsWith('!') ? compose.slice(1) : '';
  const commandAutocompleteActive = compose.startsWith('!') && commandBody.trim().length === commandBody.length && !/\s/.test(commandBody);
  const commandQuery = commandAutocompleteActive ? commandBody : '';
  const commandSuggestions = commandAutocompleteActive
    ? enabledChatCommands.filter((command) => (command.name || '').includes(commandQuery) || (command.display_name || '').includes(commandQuery)).slice(0, 30)
    : [];
  function selectCommandSuggestion(command) {
    setCompose(`!${command.name} `);
    requestAnimationFrame(() => composerTextareaRef?.current?.focus?.());
  }
  const updateBattleControl = (patch) => setBattleControlDraft?.((current) => ({ ...(current || DEFAULT_BATTLE_CONTROL_DRAFT), ...patch }));

  function autoSelectBattleParticipants(action: string) {
    if (action !== 'start' || activeMatch || !(battleParticipants.length === 2)) return {};
    return {
      participant_a_id: battleParticipants[0].id,
      participant_b_id: battleParticipants[1].id,
    };
  }

  function handleBattleActionChange(action: string) {
    updateBattleControl({ ...DEFAULT_BATTLE_CONTROL_DRAFT, action, ...autoSelectBattleParticipants(action) });
  }

  useEffect(() => {
    if (battleAction !== 'start' || activeMatch || battleParticipants.length !== 2) return;
    if (battleControlDraft?.participant_a_id && battleControlDraft?.participant_b_id) return;
    updateBattleControl(autoSelectBattleParticipants('start'));
  }, [battleAction, activeMatch?.id, battleParticipants.length]);

  const battleControlPanel = conversation?.genre_mode === 'battle' ? (
    <details className="battle-control-dock" open={battleControlOpen} onToggle={(e) => setBattleControlOpen(e.currentTarget.open)}>
      <summary className={`battle-control-fab ${activeMatch ? 'active' : ''}`} aria-label={t('배틀 컨트롤 열기')}>
        ⚔️ <span>{t(battleAction === 'none' ? '배틀' : battleAction === 'progress' ? '진행' : battleAction === 'end' ? '종료' : '시작')}</span>
      </summary>
      <span className="battle-control-compact-summary"><Badge>{battleControlSummary}</Badge></span>
      {battleControlOpen && (
        <div className="battle-control-popover">
          <div className="battle-control-panel" aria-label={t('배틀 컨트롤')}>
            <div className="battle-control-title-row"><strong>{t('배틀 컨트롤')}</strong><Button type="button" variant="ghost" size="icon" onClick={() => setBattleControlOpen(false)} aria-label={t('배틀 컨트롤 닫기')}>×</Button></div>
            <Select value={battleAction} onChange={(e) => handleBattleActionChange(e.target.value)}>
              <option value="none">{t('일반')}</option>
              <option value="start" disabled={!!activeMatch}>{t('배틀 시작')}</option>
              <option value="progress" disabled={!activeMatch}>{t('배틀 중')}</option>
              <option value="end" disabled={!activeMatch}>{t('배틀 종료')}</option>
            </Select>
            {activeMatch && <p className="battle-control-hint">{formatActiveMatch(activeMatch.participant_a_name, activeMatch.participant_b_name, locale)}</p>}
            {battleAction === 'end' && <p className="battle-control-hint">{t('종료 후 입력 모드는 일반으로 자동 전환됩니다.')}</p>}
            {battleAction === 'start' && (
              <div className="battle-control-fields">
                <label>{t('참가자 A')}
                  <Select value={battleControlDraft?.participant_a_id || ''} onChange={(e) => updateBattleControl({ participant_a_id: e.target.value })}>
                    <option value="">{t('선택')}</option>
                    {battleParticipants.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                  </Select>
                </label>
                <label>{t('참가자 B')}
                  <Select value={battleControlDraft?.participant_b_id || ''} onChange={(e) => updateBattleControl({ participant_b_id: e.target.value })}>
                    <option value="">{t('선택')}</option>
                    {battleParticipants.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                  </Select>
                </label>
              </div>
            )}
            {battleAction === 'progress' && (
              <div className="battle-control-fields battle-progress-fields">
                <span>{formatCurrentMatch(activeMatch?.participant_a_name || t('캐릭터 A'), activeMatch?.participant_b_name || t('캐릭터 B'), locale)}</span>
                <label className="battle-balance-control"><span>{t('우위 슬라이더')}</span>
                  <div className="battle-balance-labels"><b>{activeMatch?.participant_a_name || t('캐릭터 A')}</b><b>{t('중립')}</b><b>{activeMatch?.participant_b_name || t('캐릭터 B')}</b></div>
                  <Slider min="0" max="1" step="0.05" value={battleControlDraft?.progress_balance ?? 0.5} onChange={(e) => updateBattleControl({ progress_balance: Number(e.target.value) })} />
                </label>
              </div>
            )}
            {battleAction === 'end' && (
              <div className="battle-control-fields">
                <span>{formatCurrentMatch(activeMatch?.participant_a_name || t('캐릭터 A'), activeMatch?.participant_b_name || t('캐릭터 B'), locale)}</span>
                <label>{t('승자')}
                  <Select value={battleControlDraft?.winner_id || ''} onChange={(e) => updateBattleControl({ winner_id: e.target.value })}>
                    <option value="">{t('선택')}</option>
                    {battleFighters.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                  </Select>
                </label>
                <Input value={battleControlDraft?.decisive_moment || ''} onChange={(e) => updateBattleControl({ decisive_moment: e.target.value })} placeholder={t('결정적 장면')} />
              </div>
            )}
          </div>
        </div>
      )}
    </details>
  ) : null;

  return <section className="chat-shell page"><header className="chat-header"><Button type="button" variant="ghost" size="icon" className="chat-header-button chat-back-button" aria-label={t('대화방 목록으로')} onClick={() => navigate('/conversations')}>‹</Button><div className="chat-title-block"><h2>{conversation?.title || selectedConversationId}</h2><p>{status}</p></div><div className="chat-header-actions">{activeCommands.map((command) => { const label = command.display_name || command.name || t('커맨드'); return <Badge className="active-command-badge" title={t('활성 커맨드')} key={command.id || label}><span>{label}</span><button type="button" className="active-command-close" aria-label={`${label} ${t('커맨드 해제')}`} onClick={() => clearActiveCommand?.(command.id)}>×</button></Badge>; })}<Button type="button" variant="ghost" size="icon" className="chat-header-button chat-context-button" aria-label={t('대화 흐름 열기')} onClick={openConversationContextDrawer}>☰</Button></div></header><div className="chat-body-wrap">{messageSelectionMode && <div className="message-selection-toolbar"><span>{selectedMessageIds.length} {t('개 선택됨')}</span><Button type="button" variant="ghost" size="sm" onClick={clearMessageSelection}>{t('취소')}</Button><Button type="button" variant="destructive" size="sm" disabled={!selectedMessageIds.length || busy} onClick={deleteSelectedMessages}>{t('선택 삭제')}</Button></div>}<BubbleList messages={messages} characters={characters} participants={participants} conversation={conversation} onAvatarPreview={setAvatarPreview} bottomRef={chatEndRef} threadRef={chatThreadRef} onLoadOlderMessages={loadOlderMessages} hasOlderMessages={hasOlderMessages} loadingOlderMessages={loadingOlderMessages} olderPagingReady={olderPagingReady} onRetryMessage={retryMessage} renderImages={renderChatImages} onGenerateTts={generateMessageTts} onRegenerateMessage={regenerateFromMessage} onDeleteMessage={deleteMessageBubble} onUpdateMessage={updateMessageBubble} busy={busy} selectionMode={messageSelectionMode} selectedMessageIds={selectedMessageIds} onEnterSelectionMode={enterMessageSelectionMode} onToggleMessageSelection={toggleMessageSelection} onScrollAwayChange={setShowJumpToLatest} />{showJumpToLatest && <Button type="button" variant="ghost" className="jump-to-latest-button border border-rose-200 bg-white/95 text-rose-700 shadow-[0_10px_30px_rgba(244,63,94,.18)] hover:bg-rose-50 hover:text-rose-800 dark:border-slate-700 dark:bg-slate-900/95 dark:text-slate-100 dark:hover:bg-slate-800" onClick={scrollToLatest}>{t('현재 대화로 ↓')}</Button>}</div>{battleControlPanel}<form className="composer" data-modernized="채팅 컴포저 shadcn primitive marker" onSubmit={(e) => { e.preventDefault(); sendConversationMessage(); }}><Select aria-label={t('메시지 작성자')} value={speakerId} onChange={(e) => setSpeakerId(e.target.value)}>{speakerOptions.map((id) => <option key={id} value={id}>{participantName(id)}</option>)}</Select><div className="composer-input-wrap"><button type="button" className="chat-settings-button" aria-label={t('채팅 설정')} title={t('채팅 설정')} onClick={() => setChatSettingsOpen((open) => !open)}>⚙</button><ChatSettingsBubble open={chatSettingsOpen} setting={conversationRuntimeSetting} onChange={setConversationRuntimeSetting} onSave={saveConversationRuntimeSetting} saving={busy} onClose={() => setChatSettingsOpen(false)} renderImages={renderChatImages} onRenderImagesChange={setRenderChatImages} ttsEnabled={!!conversation?.tts_enabled} onTtsEnabledChange={(enabled) => setConversation((current) => ({ ...(current || {}), tts_enabled: enabled }))} onCompressNow={compressConversationNow} />{commandSuggestions.length > 0 && <><div className="command-autocomplete-backdrop" aria-hidden="true" /><div className="command-autocomplete" role="listbox" aria-label={t('활성 가능한 커맨드 목록')}>{commandSuggestions.map((command) => <button type="button" key={command.id || command.name} className="command-autocomplete-item" onClick={() => selectCommandSuggestion(command)}><strong>{command.display_name || command.name}</strong><small>{command.description || command.prompt}</small></button>)}</div></>}<Textarea ref={composerTextareaRef} rows={1} aria-label={t('메시지 입력 · Enter 줄바꿈 · Ctrl+Enter 전송')} value={compose} onChange={(e) => { setCompose(e.target.value); resizeComposerTextarea(e.currentTarget); }} onInput={(e) => resizeComposerTextarea(e.currentTarget)} onKeyDown={handleComposerKeyDown} placeholder={t('*액션묘사* 대사')} />{composeExpanded && <div className="text-editor-modal composer-editor-modal" role="dialog" aria-modal="true"><button type="button" className="text-editor-backdrop" aria-label={t('닫기')} onClick={() => setComposeExpanded(false)} /><Card className="text-editor-card composer-editor-card"><header className="text-editor-head"><div><strong>{t('메시지 크게 쓰기')}</strong><small>{t('하단 키보드에 가리지 않도록 위쪽에서 작성합니다.')}</small></div><Button type="button" variant="ghost" size="sm" onClick={() => setComposeExpanded(false)}>{t('접기')}</Button></header><Textarea className="text-editor-textarea" autoFocus value={compose} onChange={(e) => setCompose(e.target.value)} onKeyDown={handleComposerKeyDown} placeholder={t('*액션묘사* 대사')} /><footer className="text-editor-foot"><span>{compose.length}자 · {Math.max(1, compose.split('\n').length)}줄</span><Button type="button" disabled={!compose.trim() || busy} onClick={sendConversationMessage}>{t(busy ? '전송 중…' : '전송')}</Button></footer></Card></div>}</div><Button type="button" variant="ghost" size="icon" className="composer-expand-trigger" aria-label={t('메시지 크게 쓰기')} title={t('메시지 크게 쓰기')} onClick={() => setComposeExpanded(true)}>↗</Button><Button type="submit" disabled={!compose.trim() || busy}>{t(busy ? '생성 중…' : '전송')}</Button></form></section>;
}
