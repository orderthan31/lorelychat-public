import { useEffect, useState } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import { agentApi } from './api';
import type { ImageCandidate, SessionImageContext } from './types';

function ContextThumbnail({ item, candidate }: { item: SessionImageContext; candidate: ImageCandidate }) {
  const { t } = useI18n();
  const [url, setUrl] = useState('');
  const [status, setStatus] = useState<'loading' | 'ready' | 'failed'>('loading');
  useEffect(() => {
    let active = true, objectUrl = '';
    setUrl(''); setStatus('loading');
    if (candidate.available) void agentApi.candidateBlob(item.target_kind, item.target_id, candidate.id)
      .then(blob => { if (active) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); } })
      .catch(() => { if (active) setStatus('failed'); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [item.target_kind, item.target_id, candidate.id, candidate.available]);
  return <figure>
    <div className="agent-context-thumbnail" aria-busy={candidate.available && status === 'loading'}>
      {candidate.available && url && status !== 'failed' && <img src={url} alt={candidate.label} onLoad={() => setStatus('ready')} onError={() => setStatus('failed')} />}
      {(!candidate.available || status !== 'ready') && <span role="status">{t(!candidate.available ? 'agent.referenceUnavailable' : status === 'failed' ? 'agent.imageFailed' : 'agent.imageLoading')}</span>}
    </div>
    <figcaption>{candidate.label}{candidate.is_default && <small>{t('agent.currentAvatar')}</small>}</figcaption>
  </figure>;
}

export function ImageContextCard({ item, busy }: { item: SessionImageContext; busy: boolean }) {
  const { t } = useI18n();
  return <section className="agent-draft agent-image-context" data-agent-image-context={item.target_id}>
    <h3>{item.target_label || t(item.target_kind === 'world' ? '세계관' : '캐릭터')}</h3>
    <p>{t('agent.existingImages')}</p>
    {item.context.candidates.length ? <div className="agent-image-grid">{item.context.candidates.map(candidate => <ContextThumbnail key={candidate.id} item={item} candidate={candidate} />)}</div> : <p>{t('agent.noExistingImages')}</p>}
    <p role="status">{t(busy ? 'agent.imagePlanPreparing' : 'agent.imageNotGenerated')}</p>
    {!busy && <p>{t('agent.imagePlanNext')}</p>}
  </section>;
}
