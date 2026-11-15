import { useEffect, useState } from 'react';
import { Button, Input, Select, Switch, Textarea } from '../components/atoms';
import { Field } from '../components/molecules';
import { EditorActionBar } from '../components/molecules/EditorActionBar';
import { useI18n } from '../i18n/I18nProvider';
import { AgentError, AgentOpenButton } from './components';
import { agentActions, useAgentStore } from './store';
import { agentApi } from './api';
import { MCP_SCOPES, type AgentSettingsInput, type McpClient, type McpScope } from './types';

function McpSettings() {
  const { t } = useI18n(); const enabled = useAgentStore(s => s.settings?.mcp_enabled);
  const [clients, setClients] = useState<McpClient[]>([]); const [name, setName] = useState('');
  const [scopes, setScopes] = useState<McpScope[]>(['lore:read']); const [token, setToken] = useState('');
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false); const [uncertain, setUncertain] = useState(false);
  const refresh = async () => { setClients(await agentApi.clients()); };
  useEffect(() => { let active = true; void agentApi.clients().then(rows => { if (active) setClients(rows); }).catch(() => { if (active) setError('agent.requestFailed'); }); return () => { active = false; }; }, []);
  async function create() {
    if (busy || uncertain) return; setBusy(true); setError(''); setToken('');
    try { const response = await agentApi.createClient(name.trim(), scopes); setToken(response.token); setName(''); await refresh(); }
    catch { setError('agent.clientUnknown'); setUncertain(true); }
    finally { setBusy(false); }
  }
  async function revoke(id: string) {
    setBusy(true); setError('');
    try { await agentApi.revokeClient(id); await refresh(); }
    catch { setError('agent.requestFailed'); }
    finally { setBusy(false); }
  }
  return <section className="grid gap-4"><h2 className="text-base font-semibold">{t('agent.mcpClients')}</h2><p className="text-sm text-muted-foreground">{t('agent.mcpHint')}</p><pre className="whitespace-pre-wrap break-all text-xs text-muted-foreground">{JSON.stringify({ url: '/api/mcp', headers: { Authorization: 'Bearer <MCP_CLIENT_TOKEN>' } }, null, 2)}</pre>
    <AgentError value={error} />
    <Button type="button" variant="secondary" disabled={busy} onClick={() => { setBusy(true); void refresh().catch(() => setError('agent.requestFailed')).finally(() => setBusy(false)); }}>{t('agent.refresh')}</Button>
    {clients.map(client => <div className="agent-client" key={client.id}><strong>{client.name}</strong><span>{client.scopes.join(', ')}</span><span>{t(client.enabled ? 'agent.status.enabled' : 'agent.status.revoked')}</span><Button type="button" variant="ghost" disabled={busy || !client.enabled} onClick={() => void revoke(client.id)}>{t('agent.revoke')}</Button></div>)}
    {token && <section className="grid gap-3 border-y border-border py-4" role="status"><p>{t('agent.tokenOnce')}</p><Textarea aria-label={t('agent.clientToken')} autoComplete="off" spellCheck={false} readOnly value={token} /><Button type="button" variant="secondary" onClick={() => setToken('')}>{t('agent.dismissToken')}</Button></section>}
    <form className="grid gap-4 border-t border-border pt-4" autoComplete="off" onSubmit={e => { e.preventDefault(); void create(); }}>
      <Field label={t('agent.clientName')}><Input value={name} onChange={e => setName(e.target.value)} /></Field>
      <fieldset className="min-w-0 border-0 p-0"><legend className="text-sm font-semibold">{t('agent.scopes')}</legend>{MCP_SCOPES.map(scope => <label className="flex min-h-11 items-center gap-3 text-sm" key={scope}><input className="size-4 shrink-0 accent-primary focus-visible:outline-primary" type="checkbox" disabled={scope === 'lore:read'} checked={scopes.includes(scope)} onChange={e => setScopes(current => e.target.checked ? [...current, scope] : current.filter(s => s !== scope))} />{scope}</label>)}</fieldset>
      <Button type="submit" disabled={busy || uncertain || !enabled || !name.trim() || !scopes.length}>{t('agent.createClient')}</Button>
    </form>
  </section>;
}
export function AgentSettingsView() {
  const { t } = useI18n(); const s = useAgentStore();
  const [draft, setDraft] = useState<AgentSettingsInput | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [saved, setSaved] = useState(false);
  const [llmAccountId, setLlmAccountId] = useState('');
  useEffect(() => { if (s.settings) { const { enabled, llm_model_option_key, image_provider_account_id, image_model_id, mcp_enabled } = s.settings; setDraft({ enabled, llm_model_option_key, image_provider_account_id, image_model_id, mcp_enabled }); setLlmAccountId(s.settings.llm_options.find(model => model.key === llm_model_option_key)?.provider_account_id || ''); } }, [s.settings]);
  async function save() {
    if (!draft || busy) return; setBusy(true); setError(''); setSaved(false);
    try { await agentApi.saveSettings(draft); const settings = await agentApi.settings(); useAgentStore.setState({ settings }); if (!settings.enabled) s.setOpen(false); setSaved(true); }
    catch { setError('agent.saveFailed'); }
    finally { setBusy(false); }
  }
  // Older servers expose only model choices; keep account identity (not type) distinct.
  const llmAccounts = s.settings?.llm_accounts ?? Array.from(new Map((s.settings?.llm_options ?? []).map(model => [model.provider_account_id, { id: model.provider_account_id, alias: `${model.provider_type} (${model.provider_account_id})`, provider_type: model.provider_type }])).values());
  const llmOptions = s.settings?.llm_options.filter(model => model.provider_account_id === llmAccountId) ?? [];
  const account = s.settings?.image_accounts.find(a => a.id === draft?.image_provider_account_id);
  return <section className="page agent-settings grid gap-4" data-page="agent-settings"><h1 className="sr-only">{t('agent.title')}</h1>
    <AgentOpenButton />
    <AgentError value={s.settingsError} /><AgentError value={error} />
    {s.settingsError && <Button onClick={() => void agentActions.loadSettings()}>{t('agent.refresh')}</Button>}
    {!s.settings && !s.settingsError && <p role="status">{t('불러오는 중…')}</p>}
    {draft && <form className="editor-surface" onSubmit={e => { e.preventDefault(); void save(); }}><fieldset className="grid gap-4 border-0 p-0 min-w-0" disabled={busy}>
      <Switch role="switch" label={t('agent.enable')} checked={draft.enabled} onChange={e => setDraft({ ...draft, enabled: e.target.checked })} />
      <Field label={t('agent.llmAccount')}><Select aria-label={t('agent.llmAccount')} value={llmAccountId} onChange={e => { const id = e.target.value; setLlmAccountId(id); setDraft({ ...draft, llm_model_option_key: s.settings?.llm_options.some(model => model.key === draft.llm_model_option_key && model.provider_account_id === id) ? draft.llm_model_option_key : null }); }}><option value="">{t('agent.none')}</option>{llmAccounts.map(a => <option key={a.id} value={a.id}>{a.alias}</option>)}</Select></Field>
      <Field label={t('agent.llm')}><Select disabled={!llmAccountId} aria-label={t('agent.llm')} value={draft.llm_model_option_key || ''} onChange={e => setDraft({ ...draft, llm_model_option_key: e.target.value || null })}><option value="">{t('agent.none')}</option>{llmOptions.map(model => <option key={model.key} value={model.key}>{model.label}</option>)}</Select></Field>
      <p className="text-sm text-muted-foreground">{t('agent.independentModel')}</p>
      <Field label={t('agent.imageAccount')}><Select aria-label={t('agent.imageAccount')} value={draft.image_provider_account_id || ''} onChange={e => setDraft({ ...draft, image_provider_account_id: e.target.value || null, image_model_id: null })}><option value="">{t('agent.none')}</option>{s.settings?.image_accounts.map(a => <option value={a.id} key={a.id}>{a.alias}</option>)}</Select></Field>
      <Field label={t('agent.imageModel')}><Select aria-label={t('agent.imageModel')} disabled={!account} value={draft.image_model_id || ''} onChange={e => setDraft({ ...draft, image_model_id: e.target.value || null })}><option value="">{t('agent.none')}</option>{s.settings?.image_models.filter(m => m.provider_type === account?.provider_type && (!account?.image_model_ids || account.image_model_ids.includes(m.id))).map(m => <option value={m.id} key={m.id}>{m.label}</option>)}</Select></Field>
      <Switch role="switch" label={t('agent.mcpEnable')} checked={draft.mcp_enabled} onChange={e => setDraft({ ...draft, mcp_enabled: e.target.checked })} />
      <EditorActionBar label={t('저장')}><Button type="submit" disabled={busy}>{t('저장')}</Button></EditorActionBar>
    </fieldset></form>}
    {saved && <p role="status">{t('agent.saved')}</p>}
    {s.settings && <McpSettings />}
  </section>;
}
