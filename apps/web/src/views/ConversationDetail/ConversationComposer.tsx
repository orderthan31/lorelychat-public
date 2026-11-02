import { useLayoutEffect } from 'react';
import { ChatSettingsBubble } from '../../components/organisms';
import { Button, Card, Select, Textarea } from '../../components/atoms';
import { useI18n } from '../../i18n/I18nProvider';
import type { ChatCommandItem } from '../../types/chatCommands';
import type { ConversationComposerProps } from './contracts';

export function ConversationComposer({
  conversation, setConversation, busy, sendConversationMessage,
  speakerId, setSpeakerId, speakerOptions, participantName,
  chatSettingsOpen, setChatSettingsOpen, conversationRuntimeSetting,
  setConversationRuntimeSetting, saveConversationRuntimeSetting,
  renderChatImages, setRenderChatImages, compressConversationNow,
  composerTextareaRef, compose, setCompose, resizeComposerTextarea,
  handleComposerKeyDown, composeExpanded, setComposeExpanded, chatCommands = [],
}: ConversationComposerProps) {
  const { t } = useI18n();
  // Selection temporarily unmounts the form, not its draft. Restore multiline
  // height even when the text did not change while the toolbar was visible.
  useLayoutEffect(() => { resizeComposerTextarea(); }, [resizeComposerTextarea]);
  const enabledChatCommands = chatCommands.filter((command) => command.enabled !== false);
  const commandBody = compose.startsWith('!') ? compose.slice(1) : '';
  const commandAutocompleteActive = compose.startsWith('!') && commandBody.trim().length === commandBody.length && !/\s/.test(commandBody);
  const commandQuery = commandAutocompleteActive ? commandBody : '';
  const commandSuggestions = commandAutocompleteActive
    ? enabledChatCommands.filter((command) => (command.name || '').includes(commandQuery) || (command.display_name || '').includes(commandQuery)).slice(0, 30)
    : [];
  function selectCommandSuggestion(command: ChatCommandItem) {
    setCompose(`!${command.name} `);
    requestAnimationFrame(() => composerTextareaRef.current?.focus());
  }

  return (
    <form
      className="composer"
      data-modernized="채팅 컴포저 design-system primitive marker"
      onSubmit={(event) => { event.preventDefault(); sendConversationMessage(); }}
    >
      <Select aria-label={t('메시지 작성자')} value={speakerId} onChange={(event) => setSpeakerId(event.target.value)}>
        {speakerOptions.map((id) => <option key={id} value={id}>{participantName(id)}</option>)}
      </Select>
      <div className="composer-input-wrap">
        <button type="button" className="chat-settings-button" aria-label={t('채팅 설정')} title={t('채팅 설정')} onClick={() => setChatSettingsOpen((open) => !open)}>⚙</button>
        <ChatSettingsBubble
          open={chatSettingsOpen}
          setting={conversationRuntimeSetting}
          onChange={setConversationRuntimeSetting}
          onSave={saveConversationRuntimeSetting}
          saving={busy}
          onClose={() => setChatSettingsOpen(false)}
          renderImages={renderChatImages}
          onRenderImagesChange={setRenderChatImages}
          ttsEnabled={!!conversation?.tts_enabled}
          onTtsEnabledChange={(enabled) => setConversation((current) => ({ ...(current || {}), tts_enabled: enabled }))}
          onCompressNow={compressConversationNow}
        />
        {commandSuggestions.length > 0 && <>
          <div className="command-autocomplete-backdrop" aria-hidden="true" />
          <div className="command-autocomplete" role="listbox" aria-label={t('활성 가능한 커맨드 목록')}>
            {commandSuggestions.map((command) => (
              <button type="button" key={command.id || command.name} className="command-autocomplete-item" onClick={() => selectCommandSuggestion(command)}>
                <strong>{command.display_name || command.name}</strong>
                <small>{command.description || command.prompt}</small>
              </button>
            ))}
          </div>
        </>}
        <Textarea
          ref={composerTextareaRef}
          rows={1}
          aria-label={t('메시지 입력 · Enter 줄바꿈 · Ctrl+Enter 전송')}
          value={compose}
          onChange={(event) => { setCompose(event.target.value); resizeComposerTextarea(event.currentTarget); }}
          onInput={(event) => resizeComposerTextarea(event.currentTarget)}
          onKeyDown={handleComposerKeyDown}
          placeholder={t('*액션묘사* 대사')}
        />
        {composeExpanded && (
          <div className="text-editor-modal composer-editor-modal" role="dialog" aria-modal="true">
            <button type="button" className="text-editor-backdrop" aria-label={t('닫기')} onClick={() => setComposeExpanded(false)} />
            <Card className="text-editor-card composer-editor-card">
              <header className="text-editor-head">
                <div><strong>{t('메시지 크게 쓰기')}</strong><small>{t('하단 키보드에 가리지 않도록 위쪽에서 작성합니다.')}</small></div>
                <Button type="button" variant="ghost" size="sm" onClick={() => setComposeExpanded(false)}>{t('접기')}</Button>
              </header>
              <Textarea className="text-editor-textarea" autoFocus value={compose} onChange={(event) => setCompose(event.target.value)} onKeyDown={handleComposerKeyDown} placeholder={t('*액션묘사* 대사')} />
              <footer className="text-editor-foot">
                <span>{compose.length}자 · {Math.max(1, compose.split('\n').length)}줄</span>
                <Button type="button" disabled={!compose.trim() || busy} onClick={sendConversationMessage}>{t(busy ? '전송 중…' : '전송')}</Button>
              </footer>
            </Card>
          </div>
        )}
      </div>
      <Button type="button" variant="ghost" size="icon" className="composer-expand-trigger" aria-label={t('메시지 크게 쓰기')} title={t('메시지 크게 쓰기')} onClick={() => setComposeExpanded(true)}>↗</Button>
      <Button type="submit" disabled={!compose.trim() || busy}>{t(busy ? '생성 중…' : '전송')}</Button>
    </form>
  );
}
