import { useI18n } from '../i18n/I18nProvider';
import type { UiMessageKey } from '../i18n/core';

export function AgentError({ value }: { value: string }) {
  const { t } = useI18n();
  return value ? <p className="agent-error" role="alert">{value.startsWith('agent.') ? t(value as UiMessageKey) : value}</p> : null;
}
