import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ModelSettingsView } from '../src/views/ModelSettings';
import { CharacterForm, type CharacterDraft } from '../src/components/organisms/CharacterForm';
import type { RuntimeSettingValue } from '../src/components/organisms/RuntimeSettings';
import { I18nProvider } from '../src/i18n/I18nProvider';
import '../src/styles.css';
function Fixture() {
  const [setting, setSetting] = useState<RuntimeSettingValue>();
  async function refetch() {
    setSetting(await (await fetch('/api/runtime-settings/default')).json());
  }
  useEffect(() => { void refetch(); }, []);
  return <ModelSettingsView runtimeDefaultSetting={setting} setRuntimeDefaultSetting={setSetting} refetchRuntimeDefaultSetting={refetch} />;
}
function CharacterFixture() {
  const [draft, setDraft] = useState<CharacterDraft>({ name: 'Synthetic', tts_provider: 'supertonic' });
  const [sample, setSample] = useState('');
  return <><CharacterForm draft={draft} setDraft={setDraft} onSave={() => {}} characterId="synthetic-character" assetDraft={{}} setAssetDraft={() => {}}
    ttsModels={[{ key: 'vertex-owned-tts', provider: 'vertex_ai', label: 'Synthetic Cloud TTS' }]}
    ttsVoices={[{ key: 'Kore', provider: 'vertex_ai', label: 'Synthetic Cloud voice' }]}
    onTtsSample={value => setSample(JSON.stringify(value))} />
    <output data-character-sample>{sample}</output></>;
}
createRoot(document.getElementById('root')!).render(<I18nProvider><main className="shell">{new URLSearchParams(location.search).has('character') ? <CharacterFixture /> : <Fixture />}</main></I18nProvider>);
