import { Maximize2, Settings2 } from 'lucide-react';
import { useLayoutEffect } from 'react';
import { ChatSettingsBubble } from '../../components/organisms';
import { Button, Select, Textarea } from '../../components/atoms';
import { ExpandedTextDialog } from '../../components/molecules/ExpandedTextDialog';
import { formatTextMetrics } from '../../i18n/core';
import { useI18n } from '../../i18n/I18nProvider';
import type { ChatCommandItem } from '../../types/chatCommands';
import type { ConversationComposerProps } from './contracts';

export function ConversationComposer({
  conversation, setConversation, busy, sendConversationMessage,
  speakerId, setSpeakerId, speakerOptions, participantName,
  onOpenPanelSettings, chatSettingsOpen, setChatSettingsOpen, conversationRuntimeSetting, inheritedThinkingLevel,
  setConversationRuntimeSetting, saveConversationRuntimeSetting,
  renderChatImages, setRenderChatImages, compressConversationNow,
  composerTextareaRef, compose, setCompose, resizeComposerTextarea,
  handleComposerKeyDown, composeExpanded, setComposeExpanded, chatCommands = [],
}: ConversationComposerProps) {
  const { locale, t } = useI18n();
  // Selection temporarily unmounts the form, not its draft. Restore multiline
  // height even when the text did not change while the toolbar was visible.
  useLayoutEffect(() => { resizeComposerTextarea(); }, [resizeComposerTextarea]);
  useLayoutEffect(() => {
    const node = composerTextareaRef.current;
    if (!node || typeof ResizeObserver === 'undefined') return;
    let previousWidth = node.getBoundingClientRect().width;
    let resizeFrame = 0;
    const observer = new ResizeObserver(() => {
      const width = node.getBoundingClientRect().width;
      if (width === previousWidth) return;
      previousWidth = width;
      cancelAnimationFrame(resizeFrame);
      resizeFrame = requestAnimationFrame(() => { resizeFrame = 0; resizeComposerTextarea(node); });
    });
    observer.observe(node);
    return () => { observer.disconnect(); cancelAnimationFrame(resizeFrame); };
  }, [composerTextareaRef, resizeComposerTextarea]);
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
      <div className="composer-input-wrap">
        {!onOpenPanelSettings && <ChatSettingsBubble
          open={chatSettingsOpen}
          setting={conversationRuntimeSetting}
          inheritedThinkingLevel={inheritedThinkingLevel}
          onChange={setConversationRuntimeSetting}
          onSave={saveConversationRuntimeSetting}
          saving={busy}
          onClose={() => setChatSettingsOpen(false)}
          renderImages={renderChatImages}
          onRenderImagesChange={setRenderChatImages}
          ttsEnabled={!!conversation?.tts_enabled}
          onTtsEnabledChange={(enabled) => setConversation((current) => ({ ...(current || {}), tts_enabled: enabled }))}
          onCompressNow={compressConversationNow}
        />}
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
      </div>
      <Select className="composer-speaker" aria-label={t('메시지 작성자')} value={speakerId} onChange={(event) => setSpeakerId(event.target.value)}>
        {speakerOptions.map((id) => <option key={id} value={id}>{participantName(id)}</option>)}
      </Select>
      <div className="composer-tools">
        <Button type="button" variant="ghost" size="icon" className="chat-settings-button" aria-label={t('채팅 설정')} title={t('채팅 설정')} onClick={() => onOpenPanelSettings ? onOpenPanelSettings() : setChatSettingsOpen((open) => !open)}><Settings2 size={18} /></Button>
        <Button type="button" variant="ghost" size="icon" className="composer-expand-trigger" aria-label={t('메시지 크게 쓰기')} title={t('메시지 크게 쓰기')} onClick={() => setComposeExpanded(true)}><Maximize2 size={17} /></Button>
      </div>
      <Button className="composer-send" type="submit" disabled={!compose.trim() || busy}>{t(busy ? '생성 중…' : '전송')}</Button>
      <ExpandedTextDialog open={composeExpanded} onOpenChange={setComposeExpanded} title={t('메시지 크게 쓰기')} actions={<>
        <span>{formatTextMetrics(compose.length, Math.max(1, compose.split('\n').length), locale)}</span>
        <div className="flex items-center gap-2">
          <Button type="button" variant="ghost" onClick={() => setComposeExpanded(false)}>{t('접기')}</Button>
          <Button type="button" disabled={!compose.trim() || busy} onClick={sendConversationMessage}>{t(busy ? '전송 중…' : '전송')}</Button>
        </div>
      </>}>
        <Textarea className="text-editor-textarea" data-autofocus="true" value={compose} onChange={(event) => setCompose(event.target.value)} onKeyDown={handleComposerKeyDown} placeholder={t('*액션묘사* 대사')} aria-label={t('메시지 크게 쓰기')} />
      </ExpandedTextDialog>
    </form>
  );
}
