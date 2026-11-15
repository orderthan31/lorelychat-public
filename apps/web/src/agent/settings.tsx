import { useEffect, useState } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import { AgentError } from './components';
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
  return <section><h2>{t('agent.mcpClients')}</h2><p>{t('agent.mcpHint')}</p><pre>{JSON.stringify({ url: '/api/mcp', headers: { Authorization: 'Bearer <MCP_CLIENT_TOKEN>' } }, null, 2)}</pre>
    <AgentError value={error} />
    <button disabled={busy} onClick={() => { setBusy(true); void refresh().catch(() => setError('agent.requestFailed')).finally(() => setBusy(false)); }}>{t('agent.refresh')}</button>
    {clients.map(client => <div className="agent-client" key={client.id}><strong>{client.name}</strong><span>{client.scopes.join(', ')}</span><span>{t(client.enabled ? 'agent.status.enabled' : 'agent.status.revoked')}</span><button disabled={busy || !client.enabled} onClick={() => void revoke(client.id)}>{t('agent.revoke')}</button></div>)}
    {token && <section role="status"><p>{t('agent.tokenOnce')}</p><textarea aria-label={t('agent.clientToken')} autoComplete="off" spellCheck={false} readOnly value={token} /><button onClick={() => setToken('')}>{t('agent.dismissToken')}</button></section>}
    <form autoComplete="off" onSubmit={e => { e.preventDefault(); void create(); }}>
      <label>{t('agent.clientName')}<input value={name} onChange={e => setName(e.target.value)} /></label>
      <fieldset><legend>{t('agent.scopes')}</legend>{MCP_SCOPES.map(scope => <label className="agent-check" key={scope}><input type="checkbox" disabled={scope === 'lore:read'} checked={scopes.includes(scope)} onChange={e => setScopes(current => e.target.checked ? [...current, scope] : current.filter(s => s !== scope))} />{scope}</label>)}</fieldset>
      <button disabled={busy || uncertain || !enabled || !name.trim() || !scopes.length}>{t('agent.createClient')}</button>
    </form>
  </section>;
}
export function AgentSettingsView() {
  const { t } = useI18n(); const s = useAgentStore();
  const [draft, setDraft] = useState<AgentSettingsInput | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [saved, setSaved] = useState(false);
  useEffect(() => { if (s.settings) { const { enabled, llm_model_option_key, image_provider_account_id, image_model_id, mcp_enabled } = s.settings; setDraft({ enabled, llm_model_option_key, image_provider_account_id, image_model_id, mcp_enabled }); } }, [s.settings]);
  async function save() {
    if (!draft || busy) return; setBusy(true); setError(''); setSaved(false);
    try { await agentApi.saveSettings(draft); const settings = await agentApi.settings(); useAgentStore.setState({ settings }); if (!settings.enabled) s.setOpen(false); setSaved(true); }
    catch { setError('agent.saveFailed'); }
    finally { setBusy(false); }
  }
  const account = s.settings?.image_accounts.find(a => a.id === draft?.image_provider_account_id);
  return <section className="page agent-settings agent-ui" data-page="agent-settings"><h1 className="sr-only">{t('agent.title')}</h1>
    <AgentError value={s.settingsError} /><AgentError value={error} />
    {s.settingsError && <button onClick={() => void agentActions.loadSettings()}>{t('agent.refresh')}</button>}
    {!s.settings && !s.settingsError && <p role="status">{t('불러오는 중…')}</p>}
    {draft && <form onSubmit={e => { e.preventDefault(); void save(); }}><fieldset disabled={busy}>
      <label className="agent-check"><input role="switch" type="checkbox" checked={draft.enabled} onChange={e => setDraft({ ...draft, enabled: e.target.checked })} />{t('agent.enable')}</label>
      <label>{t('agent.llm')}<select aria-label={t('agent.llm')} value={draft.llm_model_option_key || ''} onChange={e => setDraft({ ...draft, llm_model_option_key: e.target.value || null })}><option value="">{t('agent.none')}</option>{s.settings?.llm_options.map(model => <option key={model.key} value={model.key}>{model.label}</option>)}</select></label>
      <p>{t('agent.independentModel')}</p>
      <label>{t('agent.imageAccount')}<select value={draft.image_provider_account_id || ''} onChange={e => setDraft({ ...draft, image_provider_account_id: e.target.value || null, image_model_id: null })}><option value="">{t('agent.none')}</option>{s.settings?.image_accounts.map(a => <option value={a.id} key={a.id}>{a.alias}</option>)}</select></label>
      <label>{t('agent.imageModel')}<select disabled={!account} value={draft.image_model_id || ''} onChange={e => setDraft({ ...draft, image_model_id: e.target.value || null })}><option value="">{t('agent.none')}</option>{s.settings?.image_models.filter(m => m.provider_type === account?.provider_type).map(m => <option value={m.id} key={m.id}>{m.label}</option>)}</select></label>
      <label className="agent-check"><input role="switch" type="checkbox" checked={draft.mcp_enabled} onChange={e => setDraft({ ...draft, mcp_enabled: e.target.checked })} />{t('agent.mcpEnable')}</label>
      <button type="submit">{t('저장')}</button>
    </fieldset></form>}
    {saved && <p role="status">{t('agent.saved')}</p>}
    {s.settings && <McpSettings />}
  </section>;
}
