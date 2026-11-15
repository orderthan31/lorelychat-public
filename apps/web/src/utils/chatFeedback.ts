import type { Translator } from '../i18n/core';

/** Pure lifecycle copy belongs to the chat header, not a panel feedback row.
 * Unknown/compound failure text must remain actionable operation feedback.
 */
export function isChatGenerationProgress(status: string, t: Translator): boolean {
  if (!status) return false;
  if (status === t('대화 생성 job 생성 중…') || status === t('재생성 중…') || status === t('대화 생성 중…')) return true;
  return ['대기 중', '응답 작성 중', '응답 재시도 중', '응답 저장 완료'].some(
    stage => status === `${t('대화 생성 중…')} ${t(stage as Parameters<Translator>[0])}`,
  );
}
