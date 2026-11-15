import { useEffect } from 'react';
import type { Translator } from '../../i18n/core';
import { useI18n } from '../../i18n/I18nProvider';
import { Button } from '../atoms';
import './ux-feedback.scss';

// Exact known completion messages only. Unknown/compound text may contain a
// failure or a required next step and must never disappear on a timer.
const completionKeys = [
  '저장 완료', '복사 완료', '기본 채팅 설정 저장 완료', '이 방 채팅 설정 저장 완료',
  '대화방 제목 변경 완료', '대화방 수정 완료', '대화방 생성 완료', '대화방 삭제 완료',
  '캐릭터 초대 완료', '캐릭터 내보내기 완료', '캐릭터 역할 수정 완료', '캐릭터 저장 완료',
  '세계관 저장 완료', '세계관 삭제 완료', '프리셋 저장 완료', '커맨드 저장 완료', '커맨드 삭제 완료',
  '유저노트 추가 완료', '유저노트 수정 완료', '유저노트 삭제 완료', '에셋 추가 완료', '에셋 삭제 완료',
  '에셋 업로드 완료', '기본 프사 변경 완료', '버블 수정 완료', '버블 삭제 완료',
  '선택한 메시지 삭제 완료', '임시 실패 버블 삭제 완료', 'Story Arc 수정 완료', 'TTS 생성 완료',
  '재생성 완료', '응답 저장 완료', '활성 커맨드 해제됨', '활성 커맨드 전체 해제됨',
  '이전 대화 불러옴', '지금 압축 완료 · 메모리/장면 요약 갱신됨',
] as const;
export function isCompletionFeedback(status: string, t: Translator): boolean {
  return completionKeys.some(key => status === t(key));
}

export function OperationFeedback({ status, onDismiss, showPersistent = true }: {
  status: string;
  onDismiss: (expected: string) => void;
  showPersistent?: boolean;
}) {
  const { t } = useI18n();
  const transient = isCompletionFeedback(status, t);
  useEffect(() => {
    if (!status || !transient) return;
    const timer = window.setTimeout(() => onDismiss(status), 5000);
    return () => window.clearTimeout(timer);
  }, [status, transient, onDismiss]);
  if (!status || (!transient && !showPersistent)) return null;
  return <section className={transient ? 'ux-operation-toast' : 'ux-operation-feedback'} role="status" aria-live="polite" aria-atomic="true">
    <p>{status}</p>
    {transient && <Button type="button" variant="ghost" onClick={() => onDismiss(status)} aria-label={t('닫기')}>{t('닫기')}</Button>}
  </section>;
}
