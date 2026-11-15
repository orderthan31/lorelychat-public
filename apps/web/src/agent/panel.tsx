import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { AgentMark } from './AgentMark';
import { AgentMarkdown } from './AgentMarkdown';
import { useI18n } from '../i18n/I18nProvider';
import type { UiMessageKey } from '../i18n/core';
import { AgentError, AgentImage } from './components';
import { agentActions, useAgentStore } from './store';
import type { AgentDraft, ContextSelection, ImageJob, ImageSelection, ImageTargetKind } from './types';

import { ImageDraftCard } from './imageDraft';

export function AgentDraftCard({ draft }: { draft: AgentDraft }) {
  if (draft.kind === 'image_generation') return <ImageDraftCard draft={draft} />;
  if (draft.kind === 'image_registration') return <ImageRegistrationCard draft={draft} />;
  return <GenericDraftCard draft={draft} />;
}
function GenericDraftCard({ draft }: { draft: AgentDraft }) {
  const { t } = useI18n();
  const { working, pending } = useAgentStore();
  const name = draft.after.name || draft.after.title;
  return <article className="agent-draft agent-proposal" data-agent-draft={draft.id}>
    <h3>{typeof name === 'string' && name.trim() ? name : t(`agent.kind.${draft.kind}` as UiMessageKey)}</h3>
    {draft.warnings.filter(warning => warning !== 'Owner approval requested for this exact preview.').map((warning, i) => <p role="note" key={i}>{warning}</p>)}
    {draft.status === 'conflict' && <p role="alert">{t('agent.conflict')}</p>}
    {draft.status === 'committed' ? <p>{t('agent.status.committed')}</p> : <button type="button" disabled={working || !!pending.length} onClick={() => agentActions.openProposal(draft)}>{t('agent.reviewProposal')}</button>}
  </article>;
}
function ImageRegistrationCard({ draft }: { draft: AgentDraft }) {
  const { t } = useI18n();
  const { working, pending, resources } = useAgentStore();
  const terminal = draft.status === 'committed' || draft.status === 'conflict';
  const target = resources.find(resource => resource.kind === draft.after.target_kind && resource.id === draft.after.target_id);
  return <article className="agent-draft" data-agent-draft={draft.id}>
    <h3>{t('agent.kind.image_registration')} · {t(`agent.status.${draft.status}` as UiMessageKey)}</h3>
    {target && <p>{target.label}</p>}
    {draft.warnings.filter(warning => warning !== 'Owner approval requested for this exact preview.').map((warning, i) => <p role="note" key={i}>{warning}</p>)}
    <p>{t(draft.after.target_kind === 'world' ? 'agent.worldRegistrationHint' : 'agent.registrationHint')}</p>
    <div className="agent-actions">
      <button disabled={working} onClick={() => void agentActions.refreshDraft(draft.id)}>{t('agent.refresh')}</button>
      {!terminal && <button disabled={working || draft.status !== 'draft'} onClick={() => void agentActions.approve(draft)}>{t('agent.approve')}</button>}
      {draft.status === 'approved' && <button disabled={working || pending.length > 0} onClick={() => void agentActions.commit(draft)}>{t('agent.commit')}</button>}
    </div>
    {draft.status === 'conflict' && <p role="alert">{t('agent.conflict')}</p>}
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
    <details><summary>{t('agent.contextAdvanced')}</summary><label>{t('agent.contextFields')}<input value={fields} placeholder="name, description" onChange={e => setFields(e.target.value)} /></label></details>
    <button type="button" disabled={!id.trim() || !fields.trim() || contexts.length >= 8} onClick={() => { useAgentStore.setState({ contexts: [...contexts, { kind, id: id.trim(), fields: fields.split(',').map(s => s.trim()).filter(Boolean) }] }); setId(''); setFields(''); }}>{t('추가')}</button>
    {contexts.map((context, index) => <div key={index}>{context.kind} · {context.id} · {context.fields.join(', ')}<button type="button" onClick={() => useAgentStore.setState({ contexts: contexts.filter((_, i) => i !== index) })}>{t('삭제')}</button></div>)}
  </details>;
}
// In-memory only: retain reading position across dock/modal and open/close mounts.
const readingPositions = new Map<string, { top: number; following: boolean }>();
export function AgentPanel() {
  const { t } = useI18n(); const s = useAgentStore();
  const session = s.sessions[s.selected];
  const activeRequest = s.pending.find(p => p.request_id === s.activeMessage && p.session_id === s.selected);
  const progress = activeRequest ? s.operations[activeRequest.request_id]?.progress : null;
  const sendBlock = !s.settings?.enabled ? 'agent.disabledHint' : !s.settings.llm_model_option_key ? 'agent.needModel' : s.working ? 'agent.working' : s.pending.length ? 'agent.uncertain' : session?.busy ? 'agent.working' : !s.input.trim() ? 'agent.needMessage' : null;
  const [draftId, setDraftId] = useState('');
  const transcript = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const sessionKey = s.selected || 'new';
  useLayoutEffect(() => {
    const node = transcript.current;
    if (!node) return;
    const saved = readingPositions.get(sessionKey);
    following.current = saved?.following ?? true;
    node.scrollTop = saved ? saved.top : node.scrollHeight;
    return () => { readingPositions.set(sessionKey, { top: node.scrollTop, following: following.current }); };
  }, [sessionKey]);
  useLayoutEffect(() => {
    if (transcript.current && following.current) transcript.current.scrollTop = transcript.current.scrollHeight;
  }, [sessionKey, session?.messages.length, session?.messages.at(-1)?.content]);
  // Image result linkage also survives a route change or session GET recovery.
  useEffect(() => {
    for (const draft of Object.values(s.drafts)) {
      const jobId = draft.result?.job_id;
      if (typeof jobId === 'string' && !s.imageJobs[jobId]) void agentActions.loadImageJob(jobId).catch(() => {});
    }
  }, [s.drafts]);
  return <div className="agent-panel-body">
    <div className="agent-toolbar"><button disabled={s.working || !!s.pending.length} onClick={() => agentActions.newChat()}>{t('agent.newSession')}</button>
    <details className="agent-advanced"><summary>{t('agent.chatAdvanced')}</summary>
    <button disabled={s.working} onClick={() => void agentActions.refresh()}>{t('agent.refresh')}</button>
    <details><summary>{t('agent.history')}</summary><label>{t('agent.sessions')}<select disabled={s.working || !!s.pending.length} value={s.selected} onChange={e => useAgentStore.setState({ selected: e.target.value, newChat: !e.target.value })}><option value="">{t('agent.newSession')}</option>{Object.values(s.sessions).map(item => <option value={item.id} key={item.id}>{(item.title && item.title !== 'Lore Agent' && item.title !== item.id ? item.title : item.messages.find(message => message.role === 'user')?.content || t('agent.untitledConversation')).slice(0, 80)}</option>)}</select></label></details>
    <details><summary>{t('agent.advancedRecovery')}</summary><label>{t('agent.draftId')}<input value={draftId} onChange={e => setDraftId(e.target.value)} /></label><button disabled={s.working || !draftId.trim()} onClick={() => void agentActions.refreshDraft(draftId.trim())}>{t('agent.openDraft')}</button></details>
    <ContextPicker />
    </details>
    </div>
    <div className="agent-transcript" ref={transcript} onScroll={event => {
      const node = event.currentTarget;
      following.current = node.scrollHeight - node.clientHeight - node.scrollTop < 48;
      readingPositions.set(sessionKey, { top: node.scrollTop, following: following.current });
    }}>
    <AgentError value={s.error} />
    {session?.last_error && <AgentError value={session.last_error} />}
    {!session?.messages.length && !s.working && !session?.busy && <div className="agent-empty"><AgentMark size={48} /></div>}
    <div className="agent-messages" aria-live="polite">{session?.messages.map(message => <article key={message.id} data-role={message.role} className="agent-message">
      {message.role !== 'user' && <AgentMark size={26} animate={false} className="agent-message-avatar" />}
      <div className="agent-message-content"><span className="sr-only">{t(message.role === 'user' ? 'agent.you' : 'agent.title')}</span>{message.role === 'user' ? <p>{message.content}</p> : <AgentMarkdown text={message.content} />}</div>
    </article>)}</div>
    {Object.values(s.drafts).filter(d => !d.session_id || d.session_id === s.selected).map(draft => <AgentDraftCard key={draft.id} draft={draft} />)}
    {Object.values(s.imageJobs).map(job => <ImageResults key={job.id} job={job} />)}
    {Object.values(s.operations).filter(operation => operation.error).map(operation => <AgentError key={operation.request_id} value={operation.error || ''} />)}
    </div>
    <form className="agent-composer" onSubmit={event => { event.preventDefault(); void agentActions.send(); }}>
      <label><span className="sr-only">{t('agent.message')}</span><textarea rows={2} placeholder={t('agent.needMessage')} value={s.input} onChange={e => useAgentStore.setState({ input: e.target.value })} /></label>
      <div className="agent-composer-actions">
      {sendBlock && sendBlock !== 'agent.needMessage' && <p id="agent-send-status" role="status">{sendBlock === 'agent.working' ? <><AgentMark size={20} active /><span className="agent-progress" data-agent-progress={progress?.stage}>{t(progress ? `agent.progress.${progress.stage}` as UiMessageKey : sendBlock)}</span></> : t(sendBlock)}</p>}
      {!!s.pending.length && !s.working && <button type="button" disabled={s.working} onClick={() => void agentActions.refresh()}>{t('agent.refresh')}</button>}
      <button aria-describedby={sendBlock && sendBlock !== 'agent.needMessage' ? 'agent-send-status' : undefined} disabled={!!sendBlock} type="submit">{t('전송')}</button>
      </div>
    </form>
  </div>;
}
