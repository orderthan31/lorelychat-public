import { useEffect, useRef, useState } from 'react';
import { Button } from '../atoms';
import type { ChatMessage } from './BubbleList';
import { useI18n } from '../../i18n/I18nProvider';
import { messageTextForCopy, writeMessageTextToClipboard } from '../../utils/messageClipboard';

type MessageSelectionToolbarProps = {
  messages: ChatMessage[];
  selectedMessageIds: string[];
  participantName: (id?: string) => string;
  busy: boolean;
  onCancel: () => void;
  onDelete: () => void;
};

export function MessageSelectionToolbar({ messages, selectedMessageIds, participantName, busy, onCancel, onDelete }: MessageSelectionToolbarProps) {
  const { t } = useI18n();
  const [feedback, setFeedback] = useState<'복사 완료' | '복사 실패' | null>(null);
  const [copying, setCopying] = useState(false);
  const selectionVersion = useRef(0);
  useEffect(() => {
    selectionVersion.current += 1;
    setFeedback(null);
  }, [selectedMessageIds]);
  useEffect(() => () => { selectionVersion.current += 1; }, []);

  async function copySelected() {
    if (!selectedMessageIds.length || copying || busy) return;
    const selected = new Set(selectedMessageIds);
    // Loaded history, not mounted virtual rows or selection order, is authoritative.
    const text = messages.filter(message => message.id && selected.has(message.id)).map(message => {
      const name = message.speaker_type === 'user' ? t('나')
        : message.speaker_type === 'system' ? t('시스템')
          : message.speaker_type === 'storytelling' ? t('스토리텔링') : participantName(message.speaker_id);
      return [name, messageTextForCopy(message, t)].filter(Boolean).join('\n');
    }).join('\n\n');
    const version = selectionVersion.current;
    setCopying(true);
    try {
      if (!text) throw new Error('No selected message text');
      await writeMessageTextToClipboard(text);
      if (version === selectionVersion.current) setFeedback('복사 완료');
    } catch {
      if (version === selectionVersion.current) setFeedback('복사 실패');
    } finally { setCopying(false); }
  }

  return <div className="message-selection-toolbar" role="toolbar" aria-label={t('메시지 선택 동작')}>
    <span className="message-selection-count"><strong>{selectedMessageIds.length} {t('개 선택됨')}</strong><small role="status" aria-live="polite">{feedback ? t(feedback) : ''}</small></span>
    <Button type="button" variant="secondary" size="sm" data-selection-action="copy" aria-label={t('선택 복사')} disabled={!selectedMessageIds.length || copying || busy} onClick={copySelected}>{t('복사')}</Button>
    <Button type="button" variant="destructive" size="sm" data-selection-action="delete" aria-label={t('선택 삭제')} disabled={!selectedMessageIds.length || busy} onClick={onDelete}>{t('삭제')}</Button>
    <Button type="button" variant="ghost" size="sm" aria-label={t('선택 취소')} disabled={busy} onClick={onCancel}>{t('취소')}</Button>
  </div>;
}
