import { useEffect, useRef, useState } from 'react';
import type { ProviderAccount } from '../../api/resources';
import { Button, Input, Select } from '../../components/atoms';
import { Field } from '../../components/molecules';
import { useI18n } from '../../i18n/I18nProvider';
import { MAX_VERTEX_CREDENTIAL_SIZE, readServiceAccountJson, validVertexConfiguration, type VertexDraft } from './vertex';

export function VertexFields({ value, onChange, onReadingChange, disabled = false }: {
  value: VertexDraft; onChange: (patch: Partial<VertexDraft>) => void; disabled?: boolean;
  onReadingChange?: (reading: boolean) => void;
}) {
  const { t } = useI18n();
  const fileInput = useRef<HTMLInputElement>(null);
  const readVersion = useRef(0);
  const [error, setError] = useState(false);
  const [reading, setReading] = useState(false);
  useEffect(() => () => { readVersion.current += 1; }, []);
  return <div className="grid min-w-0 gap-2.5" data-vertex-fields>
    <p className="m-0 text-xs leading-relaxed text-muted-foreground">{t('계정을 저장한 뒤 모델 목록을 최신화하세요. 사용 가능한 용도는 모델별 기능에 따라 달라집니다. Vertex AI에서는 전용 Decision API를 사용할 수 없습니다.')}</p>
    <Field label={t('Google Cloud 프로젝트 ID')}><Input required disabled={disabled || reading} value={value.vertex_project} placeholder="my-project" onChange={e => onChange({ vertex_project: e.target.value })} /></Field>
    <Field label={t('위치 (리전)')}><Input required disabled={disabled || reading} value={value.vertex_location} placeholder="global" onChange={e => onChange({ vertex_location: e.target.value })} /></Field>
    <p className="context-muted">{t('global 또는 모델을 지원하는 리전을 입력하세요.')}</p>
    <Field label={t('인증 방식')}><Select disabled={disabled || reading} value={value.vertex_auth_mode} onChange={e => { readVersion.current += 1; setError(false); onChange({ vertex_auth_mode: e.target.value as VertexDraft['vertex_auth_mode'], api_key: '' }); }}>
      <option value="service_account">{t('서비스 계정 JSON')}</option><option value="adc">{t('서버 ADC')}</option>
    </Select></Field>
    {value.vertex_auth_mode === 'adc' ? <p className="context-muted">{t('서버에 미리 구성된 Application Default Credentials를 사용합니다. 브라우저에서 인증 파일 경로를 입력하지 않습니다.')}</p> : <div className="grid gap-2">
      <input ref={fileInput} hidden type="file" aria-label={t('서비스 계정 JSON')} onChange={async e => {
        const file = e.currentTarget.files?.[0];
        e.currentTarget.value = '';
        if (!file) return;
        const version = ++readVersion.current;
        setError(false); setReading(true); onReadingChange?.(true); onChange({ api_key: '' });
        try {
          if (file.size > MAX_VERTEX_CREDENTIAL_SIZE) throw new Error('File too large');
          const secret = readServiceAccountJson(await file.text());
          if (readVersion.current === version) onChange({ api_key: secret });
        } catch { if (readVersion.current === version) setError(true); }
        finally { if (readVersion.current === version) { setReading(false); onReadingChange?.(false); } }
      }} />
      <Button type="button" variant="secondary" disabled={disabled || reading} onClick={() => fileInput.current?.click()}>{t('JSON 파일 선택 / 교체')}</Button>
      <p className="context-muted" role="status">{t(value.api_key ? '새 인증 파일이 준비되었습니다. 저장해야 적용됩니다.' : '파일 내용과 이름은 표시하지 않습니다. 기존 인증 정보는 새 파일을 저장할 때만 교체됩니다.')}</p>
      {value.api_key && <Button type="button" variant="ghost" disabled={disabled} onClick={() => onChange({ api_key: '' })}>{t('선택 취소')}</Button>}
      {error && <p role="alert" className="text-sm text-destructive">{t('유효한 서비스 계정 JSON 파일을 선택하세요. 최대 32KB입니다.')}</p>}
    </div>}
    <p className="m-0 text-xs leading-relaxed text-muted-foreground">{t('설정 확인은 외부 호출 없이 진행됩니다. 모델 목록 최신화는 Google Cloud에 연결하지만 실제 모델 호출의 성공을 보장하지 않습니다. 목록에 없는 모델은 ID로 수동 추가할 수 있습니다.')}</p>
  </div>;
}

export function VertexAccountEditor({ account, disabled, save, clear }: {
  account: ProviderAccount; disabled: boolean; save: (draft: VertexDraft) => Promise<boolean>; clear: () => void;
}) {
  const { t } = useI18n();
  const [value, setValue] = useState<VertexDraft>({ vertex_project: account.vertex_project || '', vertex_location: account.vertex_location || 'global', vertex_auth_mode: account.vertex_auth_mode || 'service_account', api_key: '' });
  const [reading, setReading] = useState(false);
  return <div className="grid gap-2.5">
    <VertexFields value={value} disabled={disabled} onReadingChange={setReading} onChange={patch => setValue(current => ({ ...current, ...patch }))} />
    {value.vertex_auth_mode === 'adc' && account.api_key_status === 'set' && <p className="text-sm text-muted-foreground">{t('ADC로 저장하면 기존 JSON이 제거되고 서버에 구성된 ADC를 사용합니다.')}</p>}
    <Button type="button" disabled={disabled || reading || !validVertexConfiguration(value, account.api_key_status === 'set')} onClick={async () => {
      if (await save(value)) setValue(current => ({ ...current, api_key: '' }));
    }}>{t('Vertex 설정 저장')}</Button>
    <Button type="button" variant="ghost" disabled={disabled || reading || account.api_key_status !== 'set'} onClick={clear}>{t('저장된 JSON 제거')}</Button>
  </div>;
}
