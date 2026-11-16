import { useI18n } from '../i18n/I18nProvider';
import { agentActions, imageRecoveryTargets, useAgentStore } from './store';

// Outside the transcript: uncertainty remains reachable from New chat/history.
export function RecoveryNotice() {
  const { t } = useI18n();
  const s = useAgentStore();
  const targets = imageRecoveryTargets(s);
  if (!targets.length) return null;
  return <section className="agent-recovery-notice" aria-label={t('agent.imageRequestUnknown')}>
    {targets.map(target => {
      const draft = target.draftId ? s.drafts[target.draftId] : undefined;
      const job = target.jobId ? s.imageJobs[target.jobId] : undefined;
      const session = target.sessionId ? s.sessions[target.sessionId] : undefined;
      const targetId = draft?.after.target_id || draft?.target_id || job?.plan?.target_id;
      const kind = draft?.after.target_kind || job?.plan?.target_kind || 'character';
      const resource = s.resources.find(r => r.kind === kind && r.id === targetId);
      const title = session?.title && session.title !== session.id && session.title !== 'Lore Agent' ? session.title : session?.messages.find(m => m.role === 'user')?.content;
      const label = [resource?.label, title?.slice(0, 80)].filter(Boolean).join(' · ') || t('agent.imageRequestUnknown');
      const failed = !!s.recoveryErrors[target.key];
      return <div className="agent-recovery-row" key={target.key} data-agent-recovery={target.key}>
        <div><strong>{label}</strong><p>{t(job?.outputs.length ? 'agent.imageRequestUnresolved' : 'agent.imageRequestNoOutput')}</p>
        {failed && <p role="alert">{t('agent.recoveryFailed')}</p>}</div>
        <button type="button" disabled={!!s.recoveryLoading} aria-busy={s.recoveryLoading === target.key} onClick={() => void agentActions.openRecovery(target)}>{t(s.recoveryLoading === target.key ? 'agent.recoveryOpening' : failed ? 'agent.recoveryRetry' : 'agent.viewImageRequest')}</button>
      </div>;
    })}
  </section>;
}
