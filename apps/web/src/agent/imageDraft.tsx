import { useEffect, useState } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import type { UiMessageKey } from '../i18n/core';
import { agentApi } from './api';
import { AgentError } from './components';
import { agentActions, useAgentStore } from './store';
import type { AgentDraft, ImageChoice, ImageContext, ImageTargetKind } from './types';

// Only authenticated same-origin bytes become object URLs. No external image URLs.
export function SDKReferenceImage({ draftId, targetKind = 'character', targetId = '', referenceId = '', label, onReady }: { draftId?: string; targetKind?: ImageTargetKind; targetId?: string; referenceId?: string; label: string; onReady?: (ready: boolean) => void }) {
  const [url, setUrl] = useState(''); const [error, setError] = useState('');
  useEffect(() => {
    let active = true; let objectUrl = '';
    setUrl(''); setError(''); onReady?.(false);
    const request = draftId ? agentApi.referenceBlob(draftId) : agentApi.candidateBlob(targetKind, targetId, referenceId);
    void request.then(blob => { if (active) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); } }).catch(() => { if (active) setError('agent.referenceUnavailable'); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [draftId, targetKind, targetId, referenceId, onReady]);
  return <><AgentError value={error} />{url && <img src={url} alt={label} onLoad={() => onReady?.(true)} onError={() => { setError('agent.referenceUnavailable'); onReady?.(false); }} />}</>;
}
export function ImageDraftCard({ draft }: { draft: AgentDraft }) {
  const { t } = useI18n();
  const s = useAgentStore(); const plan = draft.after;
  const context = plan.image_context as ImageContext | undefined;
  const reference = plan.reference as { source_id: string; snapshot_id: string } | null;
  const kind: ImageTargetKind = plan.target_kind === 'world' ? 'world' : 'character';
  const target = String(plan.target_id || draft.target_id || '');
  const candidates = context?.candidates || [];
  const savedMode = plan.generation_mode === 'reference' || plan.generation_mode === 'new' ? plan.generation_mode : '';
  const pendingChoice = s.imageChoices[draft.id];
  const choice: ImageChoice = pendingChoice?.version === draft.version ? pendingChoice : { version: draft.version, mode: savedMode, referenceId: reference?.source_id || '' };
  const changeChoice = (update: Partial<typeof choice>) => useAgentStore.setState(state => ({ imageChoices: { ...state.imageChoices, [draft.id]: { ...choice, ...update } } }));
  const choiceDirty = choice.mode !== savedMode || (choice.mode === 'reference' && choice.referenceId !== reference?.source_id);
  const account = s.settings?.image_accounts.find(a => a.id === plan.provider_account_id);
  const model = s.settings?.image_models.find(m => m.id === plan.model_id && m.provider_type === account?.provider_type);
  const unsupported = choice.mode === 'reference' && model?.supports_reference !== true;
  const candidate = candidates.find(c => c.id === choice.referenceId);
  const [previewReady, setPreviewReady] = useState(false);
  const key = `${draft.id}:${draft.version}:prompt`;
  const prompt = s.edits[key] ?? String(plan.prompt || '');
  const dirty = prompt !== plan.prompt;
  const terminal = draft.status === 'committed' || draft.status === 'conflict';
  const needsChoice = !!context?.has_existing_images || !!context?.requires_choice;
  const confirmed = !needsChoice || plan.owner_choice_confirmed === true;
  const ready = !!context && !!savedMode && confirmed && !choiceDirty && !dirty && !unsupported && (savedMode !== 'reference' || (!!reference && !!candidate?.available && previewReady));
  const selectedLabel = candidates.find(c => c.id === reference?.source_id)?.label || t('agent.referencePreview');
  return <article className="agent-draft agent-image-plan" data-agent-draft={draft.id}>
    <h3>{t('agent.kind.image_generation')} · {t(`agent.status.${draft.status}` as UiMessageKey)} · v{draft.version}</h3>
    <p>{t('agent.target')}: {s.resources.find(r => r.kind === kind && r.id === target)?.label || target}</p>
    <p>{t('agent.imageModel')}: {model?.label || String(plan.model_id || '—')} · {account?.alias || String(plan.provider_account_id || '—')}</p>
    <p>{t('agent.imageCount')}: {String(plan.count || 1)}</p>
    {needsChoice && <fieldset disabled={s.working || terminal}><legend>{t('agent.consistencyQuestion')}</legend>
      <label className="agent-check"><input type="radio" name={`mode-${draft.id}`} checked={choice.mode === 'reference'} onChange={() => changeChoice({ mode: 'reference' })} />{t('agent.modeReference')}</label>
      <label className="agent-check"><input type="radio" name={`mode-${draft.id}`} checked={choice.mode === 'new'} onChange={() => changeChoice({ mode: 'new' })} />{t('agent.modeNew')}</label>
      <p>{t('agent.consistencyHint')}</p>
      <div className="agent-image-grid">{candidates.map(c => <label key={c.id}>
        {c.available && <SDKReferenceImage targetKind={kind} targetId={target} referenceId={c.id} label={c.label} />}
        <span><input type="radio" aria-label={c.label} name={`reference-${draft.id}`} disabled={choice.mode !== 'reference' || !c.available} checked={choice.mode === 'reference' && choice.referenceId === c.id} onChange={() => changeChoice({ referenceId: c.id })} />{c.label}</span>
        {c.is_default && <small>{t('agent.currentAvatar')}</small>}{!c.available && <small>{t('agent.referenceUnavailable')}</small>}
      </label>)}</div>
      {unsupported && <p role="alert">{t('agent.referenceUnsupported')}</p>}
      <button disabled={!choice.mode || unsupported || (choice.mode === 'reference' && !candidate?.available) || (!choiceDirty && confirmed)} onClick={() => choice.mode && void agentActions.imageChoice(draft, choice.mode, choice.mode === 'reference' ? choice.referenceId : undefined)}>{t('agent.confirmImageChoice')}</button>
    </fieldset>}
    {!needsChoice && <p>{t('agent.modeNew')}</p>}
    {needsChoice && confirmed && savedMode && <p role="status">{t('agent.confirmedMode')}: {t(savedMode === 'reference' ? 'agent.modeReference' : 'agent.modeNew')}</p>}
    {reference && savedMode === 'reference' && <figure className="agent-reference-preview"><SDKReferenceImage key={`${draft.id}:${draft.version}:${reference.snapshot_id}`} draftId={draft.id} label={selectedLabel} onReady={setPreviewReady} /><figcaption>{t('agent.referencePreview')}: {selectedLabel}</figcaption></figure>}
    <label>{t('agent.imagePrompt')}<textarea rows={4} value={prompt} disabled={terminal || s.working} onChange={e => useAgentStore.setState(state => ({ edits: { ...state.edits, [key]: e.target.value } }))} /></label>
    {!terminal && dirty && <button disabled={s.working || !prompt.trim()} onClick={() => void agentActions.revise(draft, { prompt })}>{t('agent.revise')}</button>}
    {Object.keys((plan.options as object) || {}).length > 0 && <details><summary>{t('agent.imageOptions')}</summary><pre>{JSON.stringify(plan.options, null, 2)}</pre></details>}
    <p>{t('agent.paidWarning')}</p>
    {draft.warnings.map((warning, i) => <p role="note" key={i}>{warning}</p>)}
    <div className="agent-actions"><button disabled={s.working} onClick={() => void agentActions.refreshDraft(draft.id)}>{t('agent.refresh')}</button>
      {!terminal && <button disabled={s.working || !ready || draft.status !== 'draft'} onClick={() => void agentActions.approve(draft)}>{t('agent.approve')}</button>}
      {draft.status === 'approved' && <button disabled={s.working || !ready || !!s.pending.length} onClick={() => void agentActions.commit(draft)}>{t('agent.generatePaid')}</button>}
    </div>
    {draft.status === 'conflict' && <p role="alert">{t('agent.conflict')}</p>}
    <details><summary>{t('agent.advanced')}</summary><pre>{JSON.stringify({ plan, preview_hash: draft.preview_hash, result: draft.result }, null, 2)}</pre></details>
  </article>;
}
