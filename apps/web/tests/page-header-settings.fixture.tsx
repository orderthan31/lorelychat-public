import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ModelSettingsView } from '../src/views/ModelSettings';
import { I18nProvider } from '../src/i18n/I18nProvider';
import type { RuntimeSettingValue } from '../src/components/organisms/RuntimeSettings';
import '../src/styles.css';
import {AppShell} from '../src/components/organisms/AppShell';
import {Button} from '../src/components/atoms';
import {AgentSettingsView} from '../src/agent/settings';
import {agentActions} from '../src/agent/store';
const option = { key: 'chat', label: 'Synthetic chat', provider_account_id: 'account', provider_account_alias: 'Synthetic account' };
function Fixture() {
  useEffect(() => { void agentActions.loadSettings(); }, []);
  const [section, setSection] = useState<'runtime' | 'providers' | 'models' | 'agent'>(() => location.hash === '#providers' ? 'providers' : 'runtime');
  useEffect(() => { const sync = () => setSection(location.hash === '#providers' ? 'providers' : location.hash === '#models' ? 'models' : location.hash === '#agent' ? 'agent' : 'runtime'); window.addEventListener('hashchange', sync); return () => window.removeEventListener('hashchange', sync); }, []);
  const [value, setValue] = useState<RuntimeSettingValue>({ model_key: 'chat', compression_model_key: 'chat', fallback_model_key: null, compression_fallback_model_key: null, thinking_level: null, compression_strategy: 'quality', decision_enabled: false, options: [option], compression_options: [option] });
  const [saved, setSaved] = useState('');
  return <AppShell route={section==='agent'?{page:'agentSettings'}:{page:'modelSettings',section}} backendOnline drawerOpen={false} setDrawerOpen={()=>{}} navigate={()=>{}} secondaryHeaderActions headerActions={<Button variant="ghost">설정</Button>}>{section==='agent'?<AgentSettingsView/>:<ModelSettingsView section={section} runtimeDefaultSetting={value} setRuntimeDefaultSetting={setValue} saveRuntimeDefaultSetting={() => setSaved(JSON.stringify(value))} />}<output data-saved hidden>{saved}</output></AppShell>;
}
createRoot(document.getElementById('root')!).render(<I18nProvider><Fixture /></I18nProvider>);
