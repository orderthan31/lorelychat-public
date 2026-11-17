import { useEffect, useState } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import { agentApi } from './api';
import { AgentError } from './AgentError';

export function AgentImage({ id }: { id: string }) {
  const { t } = useI18n();
  const [url, setUrl] = useState(''); const [error, setError] = useState('');
  useEffect(() => {
    let active = true; let objectUrl = '';
    setUrl(''); setError('');
    void agentApi.imageBlob(id).then(blob => { if (active) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); } }).catch(() => { if (active) setError('agent.imageFailed'); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [id]);
  return <><AgentError value={error} />{!url && !error && <span role="status">{t('agent.imageLoading')}</span>}{url && !error && <img src={url} alt={t('agent.generatedImage')} onError={() => setError('agent.imageFailed')} />}</>;
}
