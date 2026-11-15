import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ModelSettingsView } from '../src/views/ModelSettings';
import { SettingsView } from '../src/views/Settings';
import { AgentSettingsView } from '../src/agent/settings';
import { useAgentStore } from '../src/agent/store';
import { I18nProvider, useI18n } from '../src/i18n/I18nProvider';
import { appRouter, parseRoute, pageTitle } from '../src/router/appRouter';
import { Button } from '../src/components/atoms';
import '../src/styles.css';
import '../src/agent/agent.css';
const agentSettings = { enabled: true, mcp_enabled: true, llm_model_option_key: 'chat', image_provider_account_id: 'image', image_model_id: 'image-model', llm_accounts: [{ id: 'account', alias: 'Synthetic account', provider_type: 'google' }], llm_options: [{ key: 'chat', label: 'Synthetic chat', provider_account_id: 'account', provider_type: 'google' }], image_accounts: [{ id: 'image', alias: 'Synthetic image account', provider_type: 'google' }], image_models: [{ id: 'image-model', label: 'Synthetic image model', provider_type: 'google' }] };
useAgentStore.setState({ settings: agentSettings as any });
function Fixture() {
  const { t } = useI18n();
  const [route, setRoute] = useState(() => parseRoute());
  const [value, setValue] = useState<any>({ model_key: 'chat', compression_model_key: 'chat', options: [{ key: 'chat', label: 'Synthetic chat' }], compression_options: [{ key: 'chat', label: 'Synthetic chat' }] });
  const navigate = (path: string) => { void appRouter.navigate({ to: path }); setRoute(parseRoute()); };
  useEffect(() => { const update = () => setRoute(parseRoute()); window.addEventListener('popstate', update); return () => window.removeEventListener('popstate', update); }, []);
  return <><header className="flex items-center gap-3 p-3"><Button onClick={() => navigate('/settings')}>{t('설정')}</Button><h1 data-route-title>{t(pageTitle(route))}</h1></header>
    {route.page === 'settings' && <SettingsView navigate={navigate} />}
    {route.page === 'agentSettings' && <AgentSettingsView />}
    {route.page === 'modelSettings' && <ModelSettingsView section={route.section} runtimeDefaultSetting={value} setRuntimeDefaultSetting={setValue} saveRuntimeDefaultSetting={() => {}} />}
  </>;
}
createRoot(document.getElementById('root')!).render(<I18nProvider><main className="shell"><Fixture /></main></I18nProvider>);
