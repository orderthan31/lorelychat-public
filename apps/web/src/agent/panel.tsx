import { useEffect, useState } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import type { UiMessageKey } from '../i18n/core';
import { AgentError, AgentImage, AgentUnlock } from './components';
import { agentActions, useAgentStore } from './store';
import type { AgentDraft, ContextSelection, ImageJob, ImageSelection, ImageTargetKind } from './types';

import { ImageDraftCard } from './imageDraft';

const display = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
export function AgentDraftCard({ draft }: { draft: AgentDraft }) {
  return draft.kind === 'image_generation' ? <ImageDraftCard draft={draft} /> : <GenericDraftCard draft={draft} />;
}
function GenericDraftCard({ draft }: { draft: AgentDraft }) {
  const { t } = useI18n();
  const { working, pending, edits } = useAgentStore();
  const editKey = `${draft.id}:${draft.version}`;
  const original = JSON.stringify(draft.after, null, 2);
  const value = edits[editKey] ?? original;
  const dirty = value !== original;
  let fields: Record<string, unknown> | null = null;
  try { const parsed = JSON.parse(value); if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) fields = parsed; } catch { /* Keep invalid JSON editable. */ }
  const terminal = draft.status === 'committed' || draft.status === 'conflict';
  return <article className="agent-draft" data-agent-draft={draft.id}>
    <h3>{t(`agent.kind.${draft.kind}` as UiMessageKey)} · {t(`agent.status.${draft.status}` as UiMessageKey)} · v{draft.version}</h3>
    {draft.target_id && <p>{t('agent.target')}: <code>{draft.target_id}</code></p>}
    {draft.warnings.map((warning, i) => <p role="note" key={i}>{warning}</p>)}
    <details open><summary>{t('agent.beforeAfter')}</summary>
      {Array.from(new Set([...Object.keys(draft.before || {}), ...Object.keys(draft.after)])).map(key => <div className="agent-diff" key={key}><strong>{key}</strong><del>{display(draft.before?.[key]) ?? '—'}</del><ins>{display(draft.after[key]) ?? '—'}</ins></div>)}
    </details>
    {!terminal && <details><summary>{t('agent.editFields')}</summary><label>{t('agent.fieldsJson')}<textarea rows={8} value={value} onChange={event => useAgentStore.setState(s => ({ edits: { ...s.edits, [editKey]: event.target.value } }))} /></label>
      {!fields && <AgentError value="agent.invalidJson" />}<button disabled={!fields || !dirty || working} onClick={() => fields && void agentActions.revise(draft, fields)}>{t('agent.revise')}</button>
    </details>}
    {draft.kind === 'image_generation' && <p>{t('agent.paidWarning')}</p>}
    {draft.kind === 'image_registration' && <p>{t(draft.after.target_kind === 'world' ? 'agent.worldRegistrationHint' : 'agent.registrationHint')}</p>}
    <div className="agent-actions">
      <button disabled={working} onClick={() => void agentActions.refreshDraft(draft.id)}>{t('agent.refresh')}</button>
      {!terminal && <button disabled={working || dirty || draft.status !== 'draft'} onClick={() => void agentActions.approve(draft)}>{t('agent.approve')}</button>}
      {draft.status === 'approved' && <button disabled={working || dirty || pending.length > 0} onClick={() => void agentActions.commit(draft)}>{t(draft.kind === 'image_generation' ? 'agent.generatePaid' : 'agent.commit')}</button>}
    </div>
    {draft.status === 'conflict' && <p role="alert">{t('agent.conflict')}</p>}
    {draft.result && <details><summary>{t('agent.result')}</summary><pre>{JSON.stringify(draft.result, null, 2)}</pre></details>}
  </article>;
}
function ImageResults({ job }: { job: ImageJob }) {
  const { t } = useI18n();
  const { working, pending, resources } = useAgentStore();
  const selection = useAgentStore(s => s.imageSelections[job.id]);
  const { selected, target, targetKind, label } = selection || { selected: [], target: job.plan?.target_id || '', targetKind: job.plan?.target_kind || 'character', label: '' };
  const targets = resources.filter(r => r.kind === targetKind);
  const update = (value: Partial<ImageSelection>) => useAgentStore.setState(s => ({ imageSelections: { ...s.imageSelections, [job.id]: { selected, target, targetKind, label, ...value } } }));
  const [error, setError] = useState('');
  return <section className="agent-image-results" data-agent-image-job={job.id}>
    <h3>{t('agent.images')} · {t(`agent.status.${job.status}` as UiMessageKey)}</h3>
    <AgentError value={job.error || error} />
    <button disabled={working} onClick={() => { setError(''); void agentActions.loadImageJob(job.id).catch(() => setError('agent.requestFailed')); }}>{t('agent.refresh')}</button>
    <div className="agent-image-grid">{job.outputs.map(output => <label key={output.id}><AgentImage id={output.id} /><span><input type="checkbox" checked={selected.includes(output.id)} onChange={e => update({ selected: e.target.checked ? (targetKind === 'world' ? [output.id] : [...selected, output.id]) : selected.filter(id => id !== output.id) })} />{t('agent.selectImage')}</span></label>)}</div>
    {job.status === 'completed' && <>
      <label>{t('agent.imageTargetKind')}<select aria-label={t('agent.imageTargetKind')} value={targetKind} onChange={e => update({ targetKind: e.target.value as ImageTargetKind, target: '', selected: [] })}><option value="character">{t('캐릭터')}</option><option value="world">{t('세계관')}</option></select></label>
      <label>{t('agent.imageTarget')}<select aria-label={t('agent.imageTarget')} value={target} onChange={e => update({ target: e.target.value })}><option value="">{t('선택')}</option>{targets.map(r => <option key={r.id} value={r.id}>{r.label}</option>)}</select></label>
      {targetKind === 'character' && <label>{t('agent.imageLabel')}<input value={label} onChange={e => update({ label: e.target.value })} /></label>}
      <p>{t(targetKind === 'world' ? 'agent.worldRegistrationHint' : 'agent.registrationHint')}</p><button disabled={working || !!pending.length || !selected.length || (targetKind === 'world' && selected.length !== 1) || !targets.some(r => r.id === target)} onClick={() => void agentActions.registration(job.id, selected, target, targetKind === 'world' ? {} : { label }, targetKind)}>{t('agent.stageRegistration')}</button></>}
  </section>;
}
function ContextPicker() {
  const { t } = useI18n(); const contexts = useAgentStore(s => s.contexts);
  const resources = useAgentStore(s => s.resources);
  const [kind, setKind] = useState<ContextSelection['kind']>('character'); const [id, setId] = useState(''); const [fields, setFields] = useState('');
  return <details><summary>{t('agent.context')}</summary><p>{t('agent.contextHint')}</p>
    <label>{t('agent.contextKind')}<select value={kind} onChange={e => { setKind(e.target.value as ContextSelection['kind']); setId(''); setFields(''); }}><option value="character">{t('캐릭터')}</option><option value="world">{t('세계관')}</option></select></label>
    <label>{t('agent.contextId')}<select aria-label={t('agent.contextId')} value={id} onChange={e => setId(e.target.value)}><option value="">{t('선택')}</option>{resources.filter(r => r.kind === kind).map(r => <option key={r.id} value={r.id}>{r.label}</option>)}</select></label>
    <label>{t('agent.contextFields')}<input value={fields} placeholder="name, description" onChange={e => setFields(e.target.value)} /></label>
    <button type="button" disabled={!id.trim() || !fields.trim() || contexts.length >= 8} onClick={() => { useAgentStore.setState({ contexts: [...contexts, { kind, id: id.trim(), fields: fields.split(',').map(s => s.trim()).filter(Boolean) }] }); setId(''); setFields(''); }}>{t('추가')}</button>
    {contexts.map((context, index) => <div key={index}>{context.kind} · {context.id} · {context.fields.join(', ')}<button type="button" onClick={() => useAgentStore.setState({ contexts: contexts.filter((_, i) => i !== index) })}>{t('삭제')}</button></div>)}
  </details>;
}
export function AgentPanel() {
  const { t } = useI18n(); const s = useAgentStore();
  const session = s.sessions[s.selected];
  const [draftId, setDraftId] = useState('');
  // Image result linkage also survives a route change or session GET recovery.
  useEffect(() => {
    if (!s.unlocked) return;
    for (const draft of Object.values(s.drafts)) {
      const jobId = draft.result?.job_id;
      if (typeof jobId === 'string' && !s.imageJobs[jobId]) void agentActions.loadImageJob(jobId).catch(() => {});
    }
  }, [s.drafts, s.unlocked]);
  if (!s.unlocked) return <div className="agent-panel-body"><AgentUnlock /></div>;
  return <div className="agent-panel-body">
    <div className="agent-actions"><button disabled={s.working || !!s.pending.length} onClick={() => void agentActions.createSession()}>{t('agent.newSession')}</button><button disabled={s.working} onClick={() => void agentActions.refresh()}>{t('agent.refresh')}</button></div>
    <label>{t('agent.sessions')}<select value={s.selected} onChange={e => useAgentStore.setState({ selected: e.target.value })}><option value="">{t('선택')}</option>{Object.values(s.sessions).map(item => <option value={item.id} key={item.id}>{item.title || item.id}</option>)}</select></label>
    <details><summary>{t('agent.openDraft')}</summary><label>{t('agent.draftId')}<input value={draftId} onChange={e => setDraftId(e.target.value)} /></label><button disabled={s.working || !draftId.trim()} onClick={() => void agentActions.refreshDraft(draftId.trim())}>{t('agent.openDraft')}</button></details>
    <AgentError value={s.error} />
    {!!s.pending.length && <p role="status">{t('agent.uncertain')}</p>}
    {(s.working || session?.busy) && <p role="status">{t('agent.working')}</p>}
    {session?.last_error && <AgentError value={session.last_error} />}
    <div className="agent-messages" aria-live="polite">{session?.messages.map(message => <article key={message.id} data-role={message.role}><strong>{t(message.role === 'user' ? 'agent.you' : 'agent.title')}</strong><p>{message.content}</p></article>)}</div>
    <form onSubmit={event => { event.preventDefault(); void agentActions.send(); }}>
      <ContextPicker />
      <label>{t('agent.message')}<textarea rows={3} value={s.input} onChange={e => useAgentStore.setState({ input: e.target.value })} /></label>
      {!s.settings?.llm_model_option_key && <p>{t('agent.needModel')}</p>}
      <button disabled={s.working || !!s.pending.length || session?.busy || !s.selected || !s.input.trim() || !s.settings?.llm_model_option_key} type="submit">{t('전송')}</button>
    </form>
    {Object.values(s.drafts).filter(d => !d.session_id || d.session_id === s.selected).map(draft => <AgentDraftCard key={draft.id} draft={draft} />)}
    {Object.values(s.imageJobs).map(job => <ImageResults key={job.id} job={job} />)}
    {Object.values(s.operations).map(operation => <div key={operation.request_id} role="status">{t(`agent.status.${operation.status}` as UiMessageKey)}<AgentError value={operation.error || ''} /></div>)}
  </div>;
}
