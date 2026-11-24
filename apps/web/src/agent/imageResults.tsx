import { useState } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import type { UiMessageKey } from '../i18n/core';
import { AgentError } from './AgentError';
import { AgentImage } from './AgentImage';
import { agentActions, useAgentStore } from './store';
import type { ImageJob } from './types';

export function ImageResults({ job }: { job: ImageJob }) {
  const { t } = useI18n();
  const working = useAgentStore(s => s.working);
  const pending = useAgentStore(s => s.pending.length > 0);
  const selection = useAgentStore(s => s.imageSelections[job.id]);
  const targetId = job.plan?.target_id;
  const targetKind = job.plan?.target_kind;
  const targetLabel = useAgentStore(s => s.resources.find(r => r.kind === targetKind && r.id === targetId)?.label);
  const validTarget = !!targetId && (targetKind === 'character' || targetKind === 'world');
  const completed = job.status === 'completed';
  const world = targetKind === 'world';
  const selected = (selection?.selected || []).filter(id => job.outputs.some(output => output.id === id));
  const label = selection?.label || '';
  const update = (next: { selected?: string[]; label?: string }) => useAgentStore.setState(s => ({
    imageSelections: { ...s.imageSelections, [job.id]: { selected, label, ...next } },
  }));
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  async function refresh() {
    setRefreshing(true);
    setError('');
    try { await agentActions.loadImageJob(job.id); }
    catch { setError('agent.recoveryFailed'); }
    finally { setRefreshing(false); }
  }
  return <section className="agent-image-results" data-agent-image-job={job.id} tabIndex={-1} aria-labelledby={`agent-job-heading-${job.id}`}>
    <h3 id={`agent-job-heading-${job.id}`}>{t(job.status === 'unknown' ? 'agent.imageRequestUnknown' : 'agent.images')}{job.status !== 'unknown' && ` · ${t(`agent.status.${job.status}` as UiMessageKey)}`}</h3>
    {job.status === 'unknown' && <p>{t(job.outputs.length ? 'agent.imageRequestUnresolved' : 'agent.imageRequestNoOutput')}</p>}
    <AgentError value={error || (job.status !== 'unknown' ? job.error || '' : '')} />
    {(!completed || error || !validTarget) && <button type="button" disabled={refreshing} onClick={() => void refresh()}>{t('agent.refresh')}</button>}
    <div className="agent-image-grid">{job.outputs.map(output => completed && validTarget ? <label key={output.id}>
      <AgentImage id={output.id} /><span><input type="checkbox" checked={selected.includes(output.id)} onChange={event => update({ selected: event.target.checked ? (world ? [output.id] : [...selected, output.id]) : selected.filter(id => id !== output.id) })} />{t('agent.selectImage')}</span>
    </label> : <div key={output.id}><AgentImage id={output.id} /></div>)}</div>
    {completed && (validTarget ? <>
      <p>{t('agent.imageTarget')}: {targetLabel ?? t(world ? '세계관' : '캐릭터')}</p>
      {!world && <label>{t('agent.imageLabel')}<input value={label} onChange={event => update({ label: event.target.value })} /></label>}
      <p>{t(world ? 'agent.worldRegistrationHint' : 'agent.registrationHint')}</p>
      <button type="button" disabled={working || pending || !selected.length || (world && selected.length !== 1)} onClick={() => {
        const name = label.trim();
        void agentActions.registration(job.id, selected, targetId, !world && name ? { label: name } : {}, targetKind);
      }}>{t('agent.stageRegistration')}</button>
    </> : <AgentError value="agent.imageRegistrationTargetUnavailable" />)}
  </section>;
}
