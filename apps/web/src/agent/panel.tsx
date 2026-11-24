import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { AgentMark } from './AgentMark';
import { AgentMarkdown } from './AgentMarkdown';
import { useI18n } from '../i18n/I18nProvider';
import type { UiMessageKey } from '../i18n/core';
import { AgentError } from './AgentError';
import { ImageResults } from './imageResults';
import { agentActions, conversationPending, imageRecoveryTargets, selectedImageJobs, useAgentStore } from './store';
import type { AgentDraft, ContextSelection } from './types';

import { ImageDraftCard } from './imageDraft';
import { ImageContextCard } from './imageContext';
import { RecoveryNotice } from './RecoveryNotice';
import { ProposalChanges } from './ProposalChanges';

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
    {(draft.status === 'draft' || draft.status === 'approved') && <ProposalChanges draft={draft} />}
  </article>;
}
function ImageRegistrationCard({ draft }: { draft: AgentDraft }) {
  const { t } = useI18n();
  const { working, pending, resources } = useAgentStore();
  const terminal = draft.status === 'committed' || draft.status === 'conflict';
  const target = resources.find(resource => resource.kind === draft.after.target_kind && resource.id === draft.after.target_id);
  return <article className="agent-draft" data-agent-draft={draft.id} tabIndex={-1} aria-labelledby={`agent-draft-heading-${draft.id}`}>
    <h3 id={`agent-draft-heading-${draft.id}`}>{t('agent.kind.image_registration')} · {t(`agent.status.${draft.status}` as UiMessageKey)}</h3>
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
// In-memory only: retain reading position across routes and hidden surfaces.
const readingPositions = new Map<string, { top: number; following: boolean }>();
export function AgentPanel() {
  const { t } = useI18n(); const s = useAgentStore();
  const session = s.sessions[s.selected];
  const turn = !s.localRecovery ? s.turns[s.selected] : undefined;
  const messages = session?.messages || [];
  const showLocal = turn && !turn.restored && !messages.some(m => m.role === 'user' && m.request_id === turn.id);
  const rows = showLocal ? [...messages, { id: `local:${turn.id}`, role: 'user' as const, content: turn.content, request_id: turn.id }] : messages;
  const drafts = Object.values(s.drafts).filter(d => s.localRecovery ? d.id === s.localRecovery.draftId : !d.session_id || d.session_id === s.selected);
  const recoveryTargets = imageRecoveryTargets(s);
  const recoveryOperation = (id: string) => recoveryTargets.some(target => target.requestId === id || (target.jobId && target.jobId === s.operations[id]?.result?.job_id) || (target.draftId && target.draftId === s.operations[id]?.result?.draft_id));
  const visibleOperationError = (op: (typeof s.operations)[string]) => {
    if (!op.error || op.kind === 'agent_turn' || recoveryOperation(op.request_id)) return false;
    const jobId = op.result?.job_id;
    if (typeof jobId === 'string' && s.imageJobs[jobId]?.error) return false;
    const draftId = op.result?.draft_id;
    const owner = s.pending.find(p => p.request_id === op.request_id)?.session_id || op.result?.session_id
      || (typeof draftId === 'string' ? s.drafts[draftId]?.session_id : null);
    // Never hide an unlinked critical receipt by guessing its owner.
    return !owner || owner === s.selected;
  };
  const jobs = selectedImageJobs(s);
  const contexts = (session?.image_contexts || []).filter(item => !drafts.some(d => d.kind === 'image_generation' && (d.after.target_kind || 'character') === item.target_kind && (d.after.target_id || d.target_id) === item.target_id && !!(d.after.image_context as { candidates?: unknown[] } | undefined)?.candidates?.length));
  const contentKey = JSON.stringify([contexts, drafts.map(d => [d.id, d.version, d.status]), jobs.map(j => [j.id, j.status, j.outputs.length])]);
  const activeRequest = s.pending.find(p => p.request_id === s.activeMessage && p.session_id === s.selected);
  const progress = activeRequest ? s.operations[activeRequest.request_id]?.progress : null;
  const sendBlock = !s.settings?.enabled ? 'agent.disabledHint' : !s.settings.llm_model_option_key ? 'agent.needModel' : s.working ? 'agent.working' : conversationPending(s) ? 'agent.uncertain' : session?.busy ? 'agent.working' : !s.input.trim() ? 'agent.needMessage' : null;
  const uncertain = sendBlock === 'agent.uncertain';
  const failed = (!sendBlock || sendBlock === 'agent.needMessage') && (turn?.phase === 'failed' || (!turn && session?.last_error && !session.busy));
  const unsent = !!failed && !!turn && !messages.some(m => m.role === 'user' && m.request_id === turn.id);
  const hasRecovery = s.pending.some(p => p.request_id !== s.activeMessage && p.request_id !== s.activeCreation);
  const [draftId, setDraftId] = useState('');
  const transcript = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const focusScrollTop = useRef<number | null>(null);
  const focusedToken = useRef(0);
  const recoveryAnchor = useRef<{ card: HTMLElement; offset: number } | null>(null);
  const releaseRecoveryAnchor = () => { recoveryAnchor.current = null; focusScrollTop.current = null; };
  const content = useRef<HTMLDivElement>(null);
  const sessionKey = s.selected || 'new';
  const visible = s.open && !!s.settings?.enabled;
  useLayoutEffect(() => {
    const node = transcript.current;
    if (!node || !visible) return;
    releaseRecoveryAnchor();
    const saved = readingPositions.get(sessionKey);
    following.current = saved?.following ?? true;
    node.scrollTop = saved ? saved.top : node.scrollHeight;
    const observer = new ResizeObserver(() => {
      const anchor = recoveryAnchor.current;
      if (anchor?.card.isConnected) {
        node.scrollTop += anchor.card.getBoundingClientRect().top - node.getBoundingClientRect().top - anchor.offset;
        focusScrollTop.current = node.scrollTop;
        readingPositions.set(sessionKey, { top: node.scrollTop, following: false });
      } else {
        const position = readingPositions.get(sessionKey);
        node.scrollTop = following.current ? node.scrollHeight : position?.top ?? node.scrollTop;
      }
    });
    observer.observe(node);
    if (content.current) observer.observe(content.current);
    return () => { observer.disconnect(); readingPositions.set(sessionKey, { top: node.scrollTop, following: following.current }); };
  }, [sessionKey, visible]);
  useLayoutEffect(() => {
    if (visible && transcript.current && following.current) transcript.current.scrollTop = transcript.current.scrollHeight;
  }, [sessionKey, visible, rows.length, rows.at(-1)?.content, contentKey]);
  useLayoutEffect(() => {
    const target = s.recoveryFocus;
    if (!target) { releaseRecoveryAnchor(); return; }
    const node = transcript.current;
    if (!visible || !target || !node || focusedToken.current === target.token || (target.sessionId || '') !== s.selected) return;
    const card = Array.from(node.querySelectorAll<HTMLElement>('[data-agent-image-job], [data-agent-draft]')).find(element => target.jobId ? element.dataset.agentImageJob === target.jobId : element.dataset.agentDraft === target.draftId);
    if (!card) return;
    focusedToken.current = target.token;
    following.current = false;
    card.classList.add('agent-recovery-focus');
    card.focus({ preventScroll: true });
    node.scrollTop += card.getBoundingClientRect().top - node.getBoundingClientRect().top - 12;
    recoveryAnchor.current = { card, offset: card.getBoundingClientRect().top - node.getBoundingClientRect().top };
    focusScrollTop.current = node.scrollTop;
    readingPositions.set(sessionKey, { top: node.scrollTop, following: false });
    const timer = window.setTimeout(() => card.classList.remove('agent-recovery-focus'), 2400);
    return () => { clearTimeout(timer); card.classList.remove('agent-recovery-focus'); };
  }, [s.recoveryFocus, s.selected, visible, sessionKey]);
  // Image result linkage also survives a route change or session GET recovery.
  useEffect(() => {
    for (const draft of drafts) {
      const jobId = draft.result?.job_id;
      if (typeof jobId === 'string' && !s.imageJobs[jobId]) void agentActions.loadImageJob(jobId).catch(() => {});
    }
  }, [s.drafts, s.selected]);
  return <div className="agent-panel-body">
    <div className="agent-toolbar"><button onClick={() => agentActions.newChat()}>{t('agent.newSession')}</button>
    <details className="agent-advanced"><summary>{t('agent.chatAdvanced')}</summary>
    <button disabled={s.refreshing} onClick={() => void agentActions.refresh()}>{t('agent.refresh')}</button>
    <details><summary>{t('agent.history')}</summary><label>{t('agent.sessions')}<select value={s.selected} onChange={e => agentActions.selectSession(e.target.value)}><option value="">{t('agent.newSession')}</option>{Object.values(s.sessions).map(item => <option value={item.id} key={item.id}>{(item.title && item.title !== 'Lore Agent' && item.title !== item.id ? item.title : item.messages.find(message => message.role === 'user')?.content || t('agent.untitledConversation')).slice(0, 80)}</option>)}</select></label></details>
    <details><summary>{t('agent.advancedRecovery')}</summary><label>{t('agent.draftId')}<input value={draftId} onChange={e => setDraftId(e.target.value)} /></label><button disabled={s.working || !draftId.trim()} onClick={() => void agentActions.refreshDraft(draftId.trim())}>{t('agent.openDraft')}</button></details>
    {session?.last_error && <details><summary>{t('agent.status.failed')}</summary><p>{session.last_error}</p></details>}
    <ContextPicker />
    </details>
    </div>
    <RecoveryNotice />
    <div className="agent-transcript" ref={transcript}
      onWheel={releaseRecoveryAnchor} onTouchMove={releaseRecoveryAnchor}
      onPointerDown={event => { if (event.target === event.currentTarget) releaseRecoveryAnchor(); }}
      onKeyDown={event => { if (!(event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement) && ['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) releaseRecoveryAnchor(); }}
      onScroll={event => {
      if (!visible) return;
      const node = event.currentTarget;
      if (recoveryAnchor.current || (focusScrollTop.current !== null && Math.abs(node.scrollTop - focusScrollTop.current) < 1)) following.current = false;
      else { focusScrollTop.current = null; following.current = node.scrollHeight - node.clientHeight - node.scrollTop < 48; }
      readingPositions.set(sessionKey, { top: node.scrollTop, following: following.current });
    }}>
    <div ref={content} className="agent-transcript-content">
    <AgentError value={Object.values(s.operations).some(op => op.error === s.error) ? '' : s.error} />
    {s.localRecovery && <p className="agent-local-request">{t('agent.localImageRequest')}</p>}
    {!rows.length && !contexts.length && !drafts.length && !jobs.length && !s.working && !session?.busy && <div className="agent-empty"><AgentMark size={48} /></div>}
    <div className="agent-messages" aria-live="polite">{rows.map(message => <article key={message.role === 'user' && message.request_id ? `turn:${message.request_id}` : message.id} data-role={message.role} className="agent-message">
      {message.role !== 'user' && <AgentMark size={26} animate={false} className="agent-message-avatar" />}
      <div className="agent-message-content"><span className="sr-only">{t(message.role === 'user' ? 'agent.you' : 'agent.title')}</span>{message.role === 'user' ? <p>{message.content}</p> : <AgentMarkdown text={message.content} />}</div>
    </article>)}</div>
    {contexts.map(item => <ImageContextCard key={`${session?.id}:${item.target_kind}:${item.target_id}`} item={item} busy={!!activeRequest || !!session?.busy} />)}
    {drafts.map(draft => <AgentDraftCard key={draft.id} draft={draft} />)}
    {jobs.map(job => <ImageResults key={job.id} job={job} />)}
    {Object.values(s.operations).filter(visibleOperationError).map(operation => <AgentError key={operation.request_id} value={operation.error || ''} />)}
    </div>
    </div>
    <form className="agent-composer" onSubmit={event => { event.preventDefault(); void agentActions.send(); }}>
      <label><span className="sr-only">{t('agent.message')}</span><textarea rows={2} placeholder={t('agent.needMessage')} value={s.input} onChange={e => useAgentStore.setState({ input: e.target.value })} /></label>
      <div className="agent-composer-actions">
      {failed && <p className="agent-turn-error" role="alert">{t(unsent ? 'agent.sendFailed' : 'agent.turnFailed')}</p>}
      {sendBlock && sendBlock !== 'agent.needMessage' && <p id="agent-send-status" role="status">{sendBlock === 'agent.working' ? <><AgentMark size={20} active /><span className={progress ? "agent-progress" : "agent-progress sr-only"} data-agent-progress={progress?.stage}>{t(progress ? `agent.progress.${progress.stage}` as UiMessageKey : sendBlock)}</span></> : t(uncertain ? 'agent.checkTurn' : sendBlock)}</p>}
      {hasRecovery && <button type="button" disabled={s.refreshing} onClick={() => void agentActions.refresh()}>{t('agent.refresh')}</button>}
      <button aria-describedby={sendBlock && sendBlock !== 'agent.needMessage' ? 'agent-send-status' : undefined} disabled={!!sendBlock} type="submit">{t('전송')}</button>
      </div>
    </form>
  </div>;
}
