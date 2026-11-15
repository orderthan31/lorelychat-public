import './settings-ux.scss';
import { useEffect, useMemo, useState } from 'react';
import { VertexFields, VertexAccountEditor } from './VertexFields';
import { validVertexConfiguration, vertexPayload, type VertexDraft } from './vertex';
import { ChevronDown, ChevronLeft, ChevronRight, Plus, PlugZap, RefreshCw, Save, Trash2 } from 'lucide-react';
import Swal from 'sweetalert2';
import { modelProviderApi, type ModelOption, type ProviderAccount, type ProviderType } from '../../api/resources';
import { Badge, Button, Input, Select, Switch } from '../../components/atoms';
import { Field } from '../../components/molecules';
import { RuntimeSettingsPanel, type RuntimeSettingValue } from '../../components/organisms';
import { DecisionCapabilityHint } from '../../components/organisms/RuntimeSettings';
import { useI18n } from '../../i18n/I18nProvider';
import { formatUiCount } from '../../i18n/core';
import type { Translator, UiMessageKey } from '../../i18n/core';
import { useMediaMatch } from '../../hooks/useMediaMatch';

export type ModelSettingsViewProps = {
  section?: ModelSettingsSection;
  runtimeDefaultSetting?: RuntimeSettingValue;
  setRuntimeDefaultSetting?: (value: RuntimeSettingValue) => void;
  saveRuntimeDefaultSetting?: () => void;
  refetchRuntimeDefaultSetting?: () => Promise<unknown>;
  busy?: boolean;
};

type ProviderDraft = VertexDraft & {
  provider_type: ProviderType;
  alias: string;
  api_key: string;
  base_url: string;
  preset: string;
};

const PROVIDERS: Array<{ type: ProviderType; label: string }> = [
  { type: 'google', label: 'Google Gemini' },
  { type: 'vertex_ai', label: 'Google Vertex AI' },
  { type: 'openai', label: 'OpenAI' },
  { type: 'anthropic', label: 'Anthropic' },
  { type: 'xai', label: 'xAI Grok' },
  { type: 'openrouter', label: 'OpenRouter' },
  { type: 'openai_compatible', label: 'OpenAI Compatible' },
];

const EMPTY_DRAFT: ProviderDraft = {
  provider_type: 'google',
  alias: '',
  api_key: '',
  base_url: '',
  preset: '',
  vertex_project: '',
  vertex_location: 'global',
  vertex_auth_mode: 'service_account',
};
const MODEL_PAGE_SIZE_OPTIONS = [10, 20, 50];
export type ModelSettingsSection = 'runtime' | 'providers' | 'models';
type AccountSyncResult = { status: 'success' | 'error'; message: string };


async function confirmApiKeyClear(t: Translator, vertex = false) {
  const result = await Swal.fire({
    title: t(vertex ? '저장된 JSON 제거' : 'API Key 제거'),
    text: t(vertex ? '저장된 서비스 계정 JSON을 제거합니다. 이 계정의 모델이 비활성화됩니다. 계속할까요?' : 'API Key를 제거하면 이 provider 계정의 모델이 모두 비활성화되고 기본 모델 선택지에서 사라집니다. 계정 별칭은 유지됩니다. 계속할까요?'),
    icon: 'warning',
    showCancelButton: true,
    confirmButtonText: t(vertex ? '저장된 JSON 제거' : 'API Key 제거'),
    cancelButtonText: t('취소'),
    reverseButtons: true,
    focusCancel: true,
    buttonsStyling: false,
    customClass: {
      popup: 'danger-confirm-popup',
      title: 'danger-confirm-title',
      htmlContainer: 'danger-confirm-body',
      actions: 'danger-confirm-actions',
      confirmButton: 'danger-confirm-button',
      cancelButton: 'danger-cancel-button',
    },
  });
  return result.isConfirmed;
}

function statusBadge(t: Translator, account?: ProviderAccount) {
  if (!account) return <Badge variant="outline">{t('미설정')}</Badge>;
  if (account.provider_type === 'vertex_ai') return <Badge variant="outline">{t(account.configured ? '설정됨 (호출 미검증)' : '연결 정보 필요')}</Badge>;
  if (account.last_test_status === 'failed') return <Badge variant="destructive">{t('연결 실패')}</Badge>;
  if (account.last_test_status === 'success') return <Badge variant="default">{t('연결 정상')}</Badge>;
  if (account.configured) return <Badge variant="secondary">{t('API Key 등록됨')}</Badge>;
  return <Badge variant="outline">{t('연결 정보 필요')}</Badge>;
}

function CapabilityEditor({ modalities, mimes, onChange, disabled = false }: {
  modalities: string[] | null; mimes: string[] | null; disabled?: boolean;
  onChange: (modalities: string[] | null, mimes: string[] | null) => void;
}) {
  const { t } = useI18n();
  return <div className="grid gap-2">
    <Field label={t('이미지 입력 지원')}><Select disabled={disabled} value={modalities == null ? 'unknown' : modalities.includes('image') ? 'image' : 'text'} onChange={(event) => {
      const mode = event.target.value;
      onChange(mode === 'unknown' ? null : mode === 'image' ? ['text', 'image'] : ['text'], mode === 'image' ? mimes : mode === 'text' ? [] : null);
    }}><option value="unknown">{t('이미지 지원 미확인')}</option><option value="text">{t('텍스트 전용')}</option><option value="image">{t('이미지 입력 지원')}</option></Select></Field>
    {modalities?.includes('image') && <Field label={t('허용 이미지 형식')}><Select disabled={disabled} value={mimes == null ? 'unknown' : mimes.join(',')} onChange={(event) => onChange(modalities, event.target.value === 'unknown' ? null : event.target.value.split(','))}>
      <option value="unknown">{t('미확인')}</option>
      <option value="image/png,image/jpeg">PNG / JPEG</option>
      <option value="image/png,image/jpeg,image/webp">PNG / JPEG / WebP</option>
      {mimes && !['image/png,image/jpeg', 'image/png,image/jpeg,image/webp'].includes(mimes.join(',')) && <option value={mimes.join(',')}>{mimes.join(', ') || t('없음')}</option>}
    </Select></Field>}
    <p className="context-muted">{t('수동 지정은 공식 문서로 확인하세요. 네이티브 이미지 입력에는 허용 형식이 필요합니다.')}</p>
  </div>;
}

function ManualCapabilityEditor({ option, disabled, save }: { option: ModelOption; disabled: boolean; save: (modalities: string[] | null, mimes: string[] | null) => Promise<void> }) {
  const { t } = useI18n();
  const [modalities, setModalities] = useState(option.input_modalities);
  const [mimes, setMimes] = useState(option.image_mime_types);
  useEffect(() => { setModalities(option.input_modalities); setMimes(option.image_mime_types); }, [option]);
  return <details><summary className="min-h-11 cursor-pointer">{t('이미지 기능 수동 편집')}</summary>
    <CapabilityEditor modalities={modalities} mimes={mimes} disabled={disabled} onChange={(m, f) => { setModalities(m); setMimes(f); }} />
    <Button type="button" disabled={disabled || (option.provider_type !== 'openrouter' && !!modalities?.includes('image') && !mimes?.length)} onClick={() => save(modalities, mimes)}>{t('저장')}</Button>
  </details>;
}

export function ModelSettingsView({ section = 'runtime', runtimeDefaultSetting, setRuntimeDefaultSetting, saveRuntimeDefaultSetting, refetchRuntimeDefaultSetting, busy = false }: ModelSettingsViewProps) {
  const { locale, t } = useI18n();
  const wideFilters = useMediaMatch('(min-width: 960px)');
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [accounts, setAccounts] = useState<ProviderAccount[]>([]);
  const [modelOptions, setModelOptions] = useState<ModelOption[]>([]);
  const [draft, setDraft] = useState<ProviderDraft>(EMPTY_DRAFT);
  const [selectedAccountId, setSelectedAccountId] = useState('');
  const [manualModel, setManualModel] = useState('');
  const [manualLabel, setManualLabel] = useState('');
  const [manualInputs, setManualInputs] = useState<string[] | null>(null);
  const [manualMimes, setManualMimes] = useState<string[] | null>(null);
  const [manualInputsEdited, setManualInputsEdited] = useState(false);
  const [manualCapability, setManualCapability] = useState<'chat' | 'decision'>('chat');
  const [accountApiKeys, setAccountApiKeys] = useState<Record<string, string>>({});
  const [modelQuery, setModelQuery] = useState('');
  const [modelProviderFilter, setModelProviderFilter] = useState('');
  const [modelCapabilityFilter, setModelCapabilityFilter] = useState<'runtime' | 'chat' | 'compression' | 'tts' | 'decision' | 'none' | 'all'>('runtime');
  const [modelStatusFilter, setModelStatusFilter] = useState<'all' | 'enabled' | 'disabled'>('all');
  const [modelPage, setModelPage] = useState(1);
  const [modelPageSize, setModelPageSize] = useState(10);
  const [loading, setLoading] = useState(false);
  const [syncingAccountId, setSyncingAccountId] = useState('');
  const [accountSyncResults, setAccountSyncResults] = useState<Record<string, AccountSyncResult>>({});
  const [message, setMessage] = useState('');


  const accountsByType = useMemo(() => {
    const map = new Map<ProviderType, ProviderAccount[]>();
    for (const account of accounts) {
      const list = map.get(account.provider_type) || [];
      list.push(account);
      map.set(account.provider_type, list);
    }
    return map;
  }, [accounts]);

  const filteredModelOptions = useMemo(() => {
    const query = modelQuery.trim().toLowerCase();
    return modelOptions.filter((option) => {
      const runtimeCapable = option.supports_chat || option.supports_compression || option.supports_tts || option.supports_decision;
      if (modelProviderFilter && option.provider_account_id !== modelProviderFilter) return false;
      if (modelStatusFilter === 'enabled' && !option.enabled) return false;
      if (modelStatusFilter === 'disabled' && option.enabled) return false;
      if (modelCapabilityFilter === 'runtime' && !runtimeCapable) return false;
      if (modelCapabilityFilter === 'chat' && !option.supports_chat) return false;
      if (modelCapabilityFilter === 'compression' && !option.supports_compression) return false;
      if (modelCapabilityFilter === 'tts' && !option.supports_tts) return false;
      if (modelCapabilityFilter === 'decision' && !option.supports_decision) return false;
      if (modelCapabilityFilter === 'none' && runtimeCapable) return false;
      if (!query) return true;
      const haystack = [
        option.display_label,
        option.label,
        option.model,
        option.provider_type,
        option.provider_account_alias,
        option.model_family,
        option.source,
      ].join(' ').toLowerCase();
      return haystack.includes(query);
    });
  }, [modelOptions, modelCapabilityFilter, modelProviderFilter, modelQuery, modelStatusFilter]);
  const modelPageCount = Math.max(1, Math.ceil(filteredModelOptions.length / modelPageSize));
  const visibleModelOptions = filteredModelOptions.slice((Math.min(modelPage, modelPageCount) - 1) * modelPageSize, Math.min(modelPage, modelPageCount) * modelPageSize);

  async function loadModelProviderState() {
    setLoading(true);
    try {
      const [nextAccounts, nextOptions] = await Promise.all([modelProviderApi.accounts(), modelProviderApi.options()]);
      setAccounts(nextAccounts || []);
      setModelOptions(nextOptions || []);
      setSelectedAccountId((current) => current || nextAccounts?.[0]?.id || '');
    } catch (error: any) {
      setMessage(`${t('Provider 설정 불러오기 실패 ·')} ${error.message}`);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadModelProviderState();
  }, []);


  useEffect(() => {
    setModelPage(1);
  }, [modelCapabilityFilter, modelProviderFilter, modelQuery, modelStatusFilter, modelPageSize]);

  async function refreshModelProviderAndRuntimeState() {
    await loadModelProviderState();
    await refetchRuntimeDefaultSetting?.();
  }

  async function saveProviderAccount() {
    if (!draft.alias.trim()) {
      setMessage(t('Provider 계정 별칭을 입력해주세요.'));
      return;
    }
    if (draft.provider_type === 'xai' && !draft.api_key.trim()) {
      setMessage(t('xAI Grok API Key를 입력해주세요.'));
      return;
    }
    if (draft.provider_type === 'vertex_ai' && !validVertexConfiguration(draft)) {
      setMessage(t('프로젝트, 위치 및 인증 정보를 확인하세요.'));
      return;
    }
    setLoading(true);
    try {
      const account = await modelProviderApi.createAccount({
        provider_type: draft.provider_type,
        alias: draft.alias.trim(),
        ...(draft.provider_type === 'vertex_ai' ? vertexPayload(draft) : {
          api_key: draft.api_key.trim() || undefined,
          base_url: draft.base_url.trim() || undefined,
          preset: draft.preset || undefined,
        }),
      });
      setDraft({ ...EMPTY_DRAFT, provider_type: draft.provider_type });
      if (account) {
        setSelectedAccountId(account.id);
        setModelProviderFilter(account.id);
      }
      setMessage(draft.provider_type === 'xai'
        ? t('xAI Grok 계정과 모델 목록이 저장되었습니다. 사용할 모델을 활성화해주세요.')
        : t('Provider 계정이 저장되었습니다. API key 원문은 다시 표시되지 않습니다.'));
      await refreshModelProviderAndRuntimeState();
    } catch (error: any) {
      setMessage(draft.provider_type === 'vertex_ai' ? t('Vertex 설정 저장에 실패했습니다. 설정과 인증 파일을 확인하세요.') : `${t('Provider 계정 저장 실패 ·')} ${error.message}`);
    } finally {
      setLoading(false);
    }
  }

  async function testAccount(accountId: string) {
    setLoading(true);
    try {
      const result: any = await modelProviderApi.testAccount(accountId);
      setMessage(accounts.find(account => account.id === accountId)?.provider_type === 'vertex_ai'
        ? t(result.status === 'success' ? '설정 확인 완료. 실제 인증 및 모델 호출은 검증하지 않았습니다.' : '프로젝트, 위치 및 인증 정보를 확인하세요.')
        : result.message || t('연결 테스트 완료'));
      await refreshModelProviderAndRuntimeState();
    } catch (error: any) {
      setMessage(`${t('연결 테스트 실패 ·')} ${error.message}`);
    } finally {
      setLoading(false);
    }
  }

  async function syncModels(account: ProviderAccount) {
    const knownModelIds = new Set(
      modelOptions
        .filter((option) => option.provider_account_id === account.id)
        .map((option) => option.model),
    );
    setSyncingAccountId(account.id);
    setAccountSyncResults((current) => {
      const next = { ...current };
      delete next[account.id];
      return next;
    });
    setLoading(true);
    try {
      const syncedOptions = (await modelProviderApi.syncModels(account.id)) || [];
      const discoveredCount = syncedOptions.filter((option) => option.source === 'fetched' && !knownModelIds.has(option.model)).length;
      setModelProviderFilter(account.id);
      setModelCapabilityFilter('runtime');
      setModelStatusFilter('all');
      const resultMessage = `${t('모델 목록 최신화 완료')} · ${t('신규')} ${formatUiCount(discoveredCount, 'items', locale)} · ${t('전체')} ${formatUiCount(syncedOptions.length, 'items', locale)}`;
      setMessage(`${account.alias} · ${resultMessage}`);
      setAccountSyncResults((current) => ({ ...current, [account.id]: { status: 'success', message: resultMessage } }));
      await refreshModelProviderAndRuntimeState();
    } catch (error: any) {
      const resultMessage = `${t('모델 목록 최신화 실패 ·')} ${error.message}`;
      setMessage(`${account.alias} · ${resultMessage}`);
      setAccountSyncResults((current) => ({ ...current, [account.id]: { status: 'error', message: resultMessage } }));
    } finally {
      setSyncingAccountId('');
      setLoading(false);
    }
  }

  async function saveVertexAccount(account: ProviderAccount, value: VertexDraft) {
    if (!validVertexConfiguration(value, account.api_key_status === 'set')) return false;
    setLoading(true);
    try {
      await modelProviderApi.updateAccount(account.id, vertexPayload(value));
      setMessage(t('Vertex 설정이 저장되었습니다. 실제 모델 호출은 검증하지 않았습니다.'));
      await refreshModelProviderAndRuntimeState();
      return true;
    } catch { setMessage(t('Vertex 설정 저장에 실패했습니다. 설정과 인증 파일을 확인하세요.')); return false; }
    finally { setLoading(false); }
  }

  async function updateAccountApiKey(account: ProviderAccount) {
    const apiKey = (accountApiKeys[account.id] || '').trim();
    setAccountApiKeys((current) => {
      const next = { ...current };
      delete next[account.id];
      return next;
    });
    if (!apiKey) {
      setMessage(t('새 API Key를 입력해주세요.'));
      return;
    }
    setLoading(true);
    try {
      await modelProviderApi.updateAccount(account.id, { api_key: apiKey });
      setMessage(t('API Key가 변경되었습니다. 모델 목록을 다시 불러와주세요.'));
      await refreshModelProviderAndRuntimeState();
    } catch (error: any) {
      setMessage(`${t('API Key 변경 실패 ·')} ${error.message}`);
    } finally {
      setLoading(false);
    }
  }

  async function clearAccountApiKey(account: ProviderAccount) {
    if (!(await confirmApiKeyClear(t, account.provider_type === 'vertex_ai'))) {
      return;
    }
    setAccountApiKeys((current) => {
      const next = { ...current };
      delete next[account.id];
      return next;
    });
    setLoading(true);
    try {
      await modelProviderApi.updateAccount(account.id, { clear_api_key: true });
      setMessage(t('API Key가 제거되었고 해당 계정의 모델이 비활성화되었습니다.'));
      await refreshModelProviderAndRuntimeState();
    } catch (error: any) {
      setMessage(`${t('API Key 제거 실패 ·')} ${error.message}`);
    } finally {
      setLoading(false);
    }
  }

  async function toggleModelOption(option: ModelOption, decisionCapability = false) {
    setLoading(true);
    try {
      await modelProviderApi.updateOption(option.id, decisionCapability
        ? { supports_decision: !option.supports_decision }
        : { enabled: !option.enabled });
      await refreshModelProviderAndRuntimeState();
    } catch (error: any) {
      setMessage(`${t('모델 활성화 변경 실패 ·')} ${error.message}`);
    } finally {
      setLoading(false);
    }
  }

  async function saveCapabilities(option: ModelOption, modalities: string[] | null, mimes: string[] | null) {
    setLoading(true);
    try {
      await modelProviderApi.updateOption(option.id, { input_modalities: modalities, image_mime_types: mimes, capability_source: 'manual' });
      await refreshModelProviderAndRuntimeState();
    } catch (error: any) { setMessage(error.message); }
    finally { setLoading(false); }
  }

  async function createManualModel() {
    if (!selectedAccountId || !manualModel.trim()) {
      setMessage(t('수동 모델을 추가할 Provider 계정과 모델명을 입력해주세요.'));
      return;
    }
    setLoading(true);
    try {
      const providerType = accounts.find((account) => account.id === selectedAccountId)?.provider_type;
      if (providerType === 'vertex_ai' && manualCapability !== 'chat') {
        setMessage(t('Vertex AI에서는 전용 Decision API를 사용할 수 없습니다.'));
        return;
      }
      await modelProviderApi.createManualOption({
        provider_account_id: selectedAccountId,
        model: manualModel.trim(),
        label: manualLabel.trim() || manualModel.trim(),
        model_family: providerType === 'openai_compatible' ? 'local' : providerType === 'xai' ? 'grok' : providerType === 'vertex_ai' ? 'gemini' : 'custom',
        supports_chat: manualCapability === 'chat',
        supports_compression: manualCapability === 'chat',
        supports_tts: false,
        supports_decision: manualCapability === 'decision',
        ...(manualCapability === 'decision' && manualInputsEdited ? { input_modalities: manualInputs, image_mime_types: manualMimes, capability_source: 'manual' } : {}),
      });
      setManualModel('');
      setManualLabel('');
      setManualInputs(null); setManualMimes(null); setManualInputsEdited(false);
      setMessage(t('수동 모델이 추가되었습니다.'));
      await refreshModelProviderAndRuntimeState();
    } catch (error: any) {
      setMessage(`${t('수동 모델 추가 실패 ·')} ${error.message}`);
    } finally {
      setLoading(false);
    }
  }

  return <section className="panel page model-settings-page grid gap-4" data-page="model-settings">
    <h1 className="sr-only">{t(section === 'providers' ? 'settings.providers' : section === 'models' ? 'settings.modelCatalog' : 'settings.defaultModels')}</h1>
    {message ? <p className="context-muted" role="status" aria-live="polite">{message}</p> : null}

      <div className="min-w-0">
        {section === 'runtime' && <div data-settings-section="runtime">
          <RuntimeSettingsPanel setting={runtimeDefaultSetting} onChange={setRuntimeDefaultSetting} onSave={saveRuntimeDefaultSetting} saving={busy || loading} title={t('모델 설정')} />
        </div>}

        {section === 'providers' && <div data-settings-section="providers">
    <section className="grid gap-4" data-provider-list>
          <details className="group settings-add" data-provider-add>
            <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-2 font-bold text-foreground [&::-webkit-details-marker]:hidden"><span className="flex items-center gap-2"><Plus className="size-4" aria-hidden="true" />{t('Provider 계정 추가')}</span><ChevronDown className="size-4 text-muted-foreground transition-transform group-open:rotate-180" aria-hidden="true" /></summary>
            <div className="mt-3 grid gap-2.5">
              <Field label={t('Provider')}><Select value={draft.provider_type} onChange={(e) => setDraft((current) => ({ ...current, provider_type: e.target.value as ProviderType, api_key: '' }))}>{PROVIDERS.map((provider) => <option key={provider.type} value={provider.type}>{provider.label}</option>)}</Select></Field>
              {draft.provider_type === 'xai' ? <div className="border-l-2 border-accent px-3 text-sm text-muted-foreground" data-xai-provider-help>
                {t('xAI 공식 API(')}<code>https://api.x.ai/v1</code>{t(')에서 Grok 모델 목록을 불러옵니다. 계정 저장 후 사용할 모델만 활성화하면 됩니다.')}
              </div> : null}
              <Field label={t('별칭')}><Input value={draft.alias} placeholder={draft.provider_type === 'vertex_ai' ? 'Vertex AI' : t(draft.provider_type === 'xai' ? '개인 Grok' : '개인 Gemini')} onChange={(e) => setDraft((current) => ({ ...current, alias: e.target.value }))} /></Field>
              {draft.provider_type === 'openai_compatible' ? <>
                <Field label={t('Preset')}><Select value={draft.preset} onChange={(e) => setDraft((current) => ({ ...current, preset: e.target.value }))}><option value="">{t('Custom')}</option><option value="local_gemma">Local Gemma</option><option value="lm_studio">LM Studio</option><option value="ollama">Ollama</option><option value="vllm">vLLM</option></Select></Field>
                <Field label={t('Base URL')}><Input value={draft.base_url} placeholder="http://127.0.0.1:1234/v1" onChange={(e) => setDraft((current) => ({ ...current, base_url: e.target.value }))} /></Field>
              </> : null}
              {draft.provider_type === 'vertex_ai' ? <VertexFields value={draft} disabled={loading} onChange={patch => setDraft(current => ({ ...current, ...patch }))} /> : <Field label={t('API Key')}><Input type="password" value={draft.api_key} placeholder={draft.provider_type === 'openai_compatible' ? t('선택 사항') : draft.provider_type === 'xai' ? t('xAI API Key') : t('저장 후 다시 표시되지 않음')} onChange={(e) => setDraft((current) => ({ ...current, api_key: e.target.value }))} /></Field>}
              <Button type="button" disabled={loading || (draft.provider_type === 'vertex_ai' && !validVertexConfiguration(draft))} onClick={saveProviderAccount}><Save className="size-4" aria-hidden="true" />{t(loading ? '처리 중...' : 'Provider 저장')}</Button>
            </div>
          </details>
        <div className="grid gap-4">
          <div className="divide-y divide-border border-y border-border">
            {!accounts.length && <p className="context-muted py-3">{t('아직 등록된 계정이 없습니다.')}</p>}
            {PROVIDERS.filter((provider) => accountsByType.get(provider.type)?.length).map((provider) => {
              const providerAccounts = accountsByType.get(provider.type) || [];
              return <section key={provider.type} className="grid gap-2 py-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <h3 className="m-0 text-base font-black text-foreground">{provider.label}</h3>

                </div>
                <div className="mt-3 grid gap-2">
                  {providerAccounts.length ? providerAccounts.map((account) => {
                    const syncResult = accountSyncResults[account.id];
                    return <article key={account.id} className="grid gap-2 border-t border-border py-3 first:border-t-0 first:pt-0" data-provider-account-card>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2"><p className="m-0 font-bold text-foreground">{account.alias}</p>{statusBadge(t, account)}</div>
                        {(account.provider_type === 'vertex_ai' || account.api_key_hint) && <p className="context-muted">{t(account.provider_type === 'vertex_ai' ? '인증 방식' : 'Key')} {account.provider_type === 'vertex_ai' ? (account.vertex_auth_mode === 'adc' ? t('서버 ADC') : t(account.api_key_status === 'set' ? '서비스 계정 JSON 등록됨' : '미등록')) : account.api_key_status === 'set' ? account.api_key_hint || t('등록됨') : t('미등록')}</p>}
                      </div>
                      <Button type="button" size="sm" variant="secondary" disabled={loading || !account.configured} data-provider-refresh-account={account.id} onClick={() => syncModels(account)}>
                        <RefreshCw className={`size-3.5 ${syncingAccountId === account.id ? 'animate-spin' : ''}`} aria-hidden="true" />
                        {t(syncingAccountId === account.id ? '모델 목록 최신화 중...' : '모델 목록 최신화')}
                      </Button>
                    </div>
                    {syncResult ? <p
                      className={`m-0 text-xs leading-relaxed ${syncResult.status === 'error' ? 'text-destructive' : 'text-primary'}`}
                      data-provider-refresh-result={syncResult.status}
                      role={syncResult.status === 'error' ? 'alert' : 'status'}
                    >{syncResult.message}</p> : null}
                    <details className="group">
                      <summary className="flex min-h-9 cursor-pointer list-none items-center justify-between gap-2 text-sm font-bold text-muted-foreground [&::-webkit-details-marker]:hidden">
                        <span>{t('연결 정보 관리')}</span>
                        <ChevronDown className="size-4 shrink-0 transition-transform group-open:rotate-180" aria-hidden="true" />
                      </summary>
                      <div className="mt-2 grid gap-2.5 border-t border-border/70 pt-3">
                      {account.provider_type === 'vertex_ai' ? <VertexAccountEditor account={account} disabled={loading} save={value => saveVertexAccount(account, value)} clear={() => clearAccountApiKey(account)} /> : <div className="grid gap-2 min-[760px]:grid-cols-[minmax(0,1fr)_auto_auto] min-[760px]:items-end">
                        <Field label={t('API Key')}><Input type="password" value={accountApiKeys[account.id] || ''} placeholder={t('새 API Key 입력')} onChange={(e) => setAccountApiKeys((current) => ({ ...current, [account.id]: e.target.value }))} /></Field>
                        <Button type="button" size="sm" variant="secondary" disabled={loading || !(accountApiKeys[account.id] || '').trim()} onClick={() => updateAccountApiKey(account)}><Save className="size-3.5" aria-hidden="true" />{t('API Key 변경')}</Button>
                        <Button type="button" size="sm" variant="ghost" disabled={loading || account.api_key_status !== 'set'} onClick={() => clearAccountApiKey(account)}><Trash2 className="size-3.5" aria-hidden="true" />{t('API Key 제거')}</Button>
                      </div>}
                      <div className="flex flex-wrap gap-2">
                        <Button type="button" size="sm" variant="ghost" disabled={loading} onClick={() => testAccount(account.id)}><PlugZap className="size-3.5" aria-hidden="true" />{t(account.provider_type === 'vertex_ai' ? '설정 확인 (호출 없음)' : '연결 테스트')}</Button>
                      </div>
                      </div>
                    </details>
                  </article>;
                  }) : <p className="context-muted">{t('아직 등록된 계정이 없습니다.')}</p>}
                </div>
              </section>;
            })}
          </div>


        </div>
    </section>
        </div>}

        {section === 'models' && <div data-settings-section="models">
    <section className="grid gap-3" data-model-list>
        <details className="group settings-add" data-model-add>
          <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-2 font-bold text-foreground [&::-webkit-details-marker]:hidden"><span className="flex items-center gap-2"><Plus className="size-4" aria-hidden="true" />{t('수동 추가')}</span><ChevronDown className="size-4 text-muted-foreground transition-transform group-open:rotate-180" aria-hidden="true" /></summary>
          <div className="model-manual-fields">
            <Field label={t('Provider 계정')}><Select value={selectedAccountId} onChange={(e) => { setSelectedAccountId(e.target.value); setManualCapability('chat'); }}><option value="">{t('계정 선택')}</option>{accounts.map((account) => <option key={account.id} value={account.id}>{account.alias}</option>)}</Select></Field>
            <Field label={t('용도')}><Select value={manualCapability} onChange={(e) => setManualCapability(e.target.value as typeof manualCapability)}><option value="chat">{t('채팅')} · {t('압축')}</option>{accounts.find(account => account.id === selectedAccountId)?.provider_type !== 'vertex_ai' && <option value="decision">{t('의사결정')}</option>}</Select></Field>
            {manualCapability === 'decision' && <CapabilityEditor modalities={manualInputs} mimes={manualMimes} disabled={loading} onChange={(m, f) => { setManualInputs(m); setManualMimes(f); setManualInputsEdited(true); }} />}
            <Field label={t('모델 ID')}><Input value={manualModel} placeholder="gemma-local" onChange={(e) => setManualModel(e.target.value)} /></Field>
            <Field label={t('표시명')}><Input value={manualLabel} placeholder="Local Gemma" onChange={(e) => setManualLabel(e.target.value)} /></Field>
            <Button type="button" variant="secondary" disabled={loading || (manualCapability === 'decision' && manualInputsEdited && accounts.find((account) => account.id === selectedAccountId)?.provider_type !== 'openrouter' && !!manualInputs?.includes('image') && !manualMimes?.length)} onClick={createManualModel}><Plus className="size-4" aria-hidden="true" />{t('수동 추가')}</Button>
          </div>
        </details>
        <div className="model-search-toolbar">
          <Field label={t('검색')}><Input value={modelQuery} placeholder={t('모델명, provider, 계정')} onChange={(e) => setModelQuery(e.target.value)} /></Field>
          {!wideFilters && <Button type="button" variant="ghost" data-model-filter-toggle aria-expanded={filtersOpen} aria-controls="model-filter-fields" onClick={() => setFiltersOpen(open => !open)}>{t('필터')}</Button>}
        </div>
        <div className="model-filter-fields" id="model-filter-fields" data-model-filter-fields hidden={!wideFilters && !filtersOpen}>
          <Field label={t('Provider 계정')}><Select value={modelProviderFilter} onChange={(e) => setModelProviderFilter(e.target.value)}><option value="">{t('전체 계정')}</option>{accounts.map((account) => <option key={account.id} value={account.id}>{account.alias}</option>)}</Select></Field>
          <Field label={t('용도')}><Select value={modelCapabilityFilter} onChange={(e) => setModelCapabilityFilter(e.target.value as typeof modelCapabilityFilter)}><option value="runtime">{t('런타임 가능')}</option><option value="chat">{t('채팅')}</option><option value="compression">{t('압축')}</option><option value="tts">{t('TTS')}</option><option value="decision">{t('의사결정')}</option><option value="none">{t('런타임 제외')}</option><option value="all">{t('전체')}</option></Select></Field>
          <Field label={t('상태')}><Select value={modelStatusFilter} onChange={(e) => setModelStatusFilter(e.target.value as typeof modelStatusFilter)}><option value="all">{t('전체')}</option><option value="enabled">{t('활성')}</option><option value="disabled">{t('비활성')}</option></Select></Field>

        </div>
        <div className="divide-y divide-border border-y border-border">
          {visibleModelOptions.length ? visibleModelOptions.map((option) => <div key={option.id} className="grid min-h-[72px] grid-cols-[minmax(0,1fr)_48px] items-center gap-2 py-2" data-model-row>
            <div className="min-w-0">
              <div className="grid min-w-0 gap-0.5">
                <h3 className="m-0 min-w-0 truncate text-sm font-bold leading-5 text-foreground" title={option.label}>{option.label}</h3>
                <p className="m-0 truncate text-[11px] leading-4 text-muted-foreground" title={option.provider_account_alias || option.provider_type}>{option.provider_account_alias || option.provider_type}</p>
                <details className="model-help"><summary>{t('상세')}</summary><code className="break-all text-xs">{option.model}</code></details>
                <p className="m-0 truncate text-[11px] leading-4 text-muted-foreground">{[option.supports_chat ? t('채팅') : '', option.supports_compression ? t('압축') : '', option.supports_tts ? t('TTS') : '', option.supports_decision ? t('의사결정') : '', !option.supports_chat && !option.supports_compression && !option.supports_tts && !option.supports_decision ? t('런타임 제외') : ''].filter(Boolean).join(' · ')}</p>
                {option.supports_decision && <details className="model-help"><summary>{t('이미지 지원 정보')}</summary><DecisionCapabilityHint option={option} /><ManualCapabilityEditor option={option} disabled={loading} save={(m, f) => saveCapabilities(option, m, f)} /></details>}
              </div>
            </div>
            {option.source === 'manual' && ['google', 'openrouter', 'openai', 'openai_compatible'].includes(option.provider_type) ? <Switch compact checked={!!option.supports_decision} disabled={loading} label={t('의사결정')} onChange={() => toggleModelOption(option, true)} /> : null}
            <Switch compact checked={!!option.enabled} disabled={loading} aria-label={`${option.display_label} ${t(option.enabled ? '비활성화' : '활성화')}`} title={t(option.enabled ? '비활성화' : '활성화')} onChange={() => toggleModelOption(option)} />
          </div>) : <div className="py-8 text-center text-sm text-muted-foreground">{t(modelOptions.length ? '조건에 맞는 모델이 없습니다.' : 'Provider 계정을 저장한 뒤 모델 목록을 불러오면 여기에 표시됩니다.')}</div>}
        </div>
        <div className="model-page-size"><Field label={t('표시 개수')}><Select value={String(modelPageSize)} onChange={(e) => setModelPageSize(Number(e.target.value))}>{MODEL_PAGE_SIZE_OPTIONS.map((size) => <option key={size} value={size}>{formatUiCount(size, 'items', locale)}</option>)}</Select></Field></div>
        {filteredModelOptions.length ? <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <p className="context-muted">{t('페이지')} {Math.min(modelPage, modelPageCount)} / {modelPageCount}</p>
          <div className="flex gap-2">
            <Button type="button" size="sm" variant="ghost" disabled={modelPage <= 1} onClick={() => setModelPage((page) => Math.max(1, page - 1))}><ChevronLeft className="size-3.5" aria-hidden="true" />{t('이전')}</Button>
            <Button type="button" size="sm" variant="ghost" disabled={modelPage >= modelPageCount} onClick={() => setModelPage((page) => Math.min(modelPageCount, page + 1))}>{t('다음')}<ChevronRight className="size-3.5" aria-hidden="true" /></Button>
          </div>
        </div>
          : null}


    </section>
        </div>}
      </div>
  </section>;
}
