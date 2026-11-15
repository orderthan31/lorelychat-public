import { useEffect, useState, type ReactNode } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { Bot, X } from 'lucide-react';
import { useI18n } from '../i18n/I18nProvider';
import type { UiMessageKey } from '../i18n/core';
import { agentActions, useAgentStore } from './store';
import { agentApi } from './api';
import { AgentPanel } from './panel';
import './agent.css';

export function AgentProvider({ children }: { children: ReactNode }) {
  useEffect(() => { void agentActions.loadSettings(); }, []);
  return <>{children}</>;
}
export function AgentError({ value }: { value: string }) {
  const { t } = useI18n();
  return value ? <p className="agent-error" role="alert">{value.startsWith('agent.') ? t(value as UiMessageKey) : value}</p> : null;
}
export function AgentUnlock() {
  const { t } = useI18n();
  const { settings, unlocked, working, error } = useAgentStore();
  const [key, setKey] = useState('');
  if (unlocked) return null;
  return <section className="agent-unlock">
    {!settings?.owner_configured ? <p role="status">{t('agent.ownerSetup')}</p> : <form onSubmit={e => { e.preventDefault(); const value = key; setKey(''); void agentActions.unlock(value); }} autoComplete="off">
      <label>{t('agent.ownerKey')}<input type="password" name="lore-owner-unlock" autoComplete="off" spellCheck={false} value={key} onChange={e => setKey(e.target.value)} /></label>
      <p>{t('agent.ownerHint')}</p>
      <button disabled={working || !key}>{t('agent.unlock')}</button>
    </form>}
    <AgentError value={error} />
  </section>;
}
let launcherElement: HTMLButtonElement | null = null;
export function AgentLauncher() {
  const { t } = useI18n();
  const enabled = useAgentStore(s => s.settings?.enabled);
  const open = useAgentStore(s => s.open);
  if (!enabled) return null;
  return <button type="button" className="agent-launcher" aria-label={t('agent.title')} aria-expanded={open} onClick={event => { launcherElement = event.currentTarget; useAgentStore.getState().setOpen(!open); }}><Bot size={20} aria-hidden="true" /></button>;
}
export function AgentSurface() {
  const { t } = useI18n();
  const open = useAgentStore(s => s.open && !!s.settings?.enabled);
  const [wide, setWide] = useState(() => typeof window !== 'undefined' && window.matchMedia('(min-width: 1100px)').matches);
  useEffect(() => {
    const media = window.matchMedia('(min-width: 1100px)');
    const update = () => setWide(media.matches); media.addEventListener('change', update); update();
    return () => media.removeEventListener('change', update);
  }, []);
  const close = () => useAgentStore.getState().setOpen(false);
  const heading = <header className="agent-panel-header"><h2>{t('agent.title')}</h2><button type="button" aria-label={t('닫기')} onClick={close}><X size={20} /></button></header>;
  if (wide) return <aside className="agent-dock agent-ui" hidden={!open} aria-label={t('agent.title')}>{heading}<AgentPanel /></aside>;
  return <Dialog.Root open={open} onOpenChange={value => useAgentStore.getState().setOpen(value)}><Dialog.Portal><Dialog.Overlay className="agent-overlay" /><Dialog.Content className="agent-modal agent-ui" onCloseAutoFocus={event => { event.preventDefault(); launcherElement?.focus(); }}>
    <Dialog.Title className="sr-only">{t('agent.title')}</Dialog.Title><Dialog.Description className="sr-only">{t('agent.workspace')}</Dialog.Description>
    {heading}<AgentPanel />
  </Dialog.Content></Dialog.Portal></Dialog.Root>;
}
export function AgentImage({ id }: { id: string }) {
  const { t } = useI18n();
  const [url, setUrl] = useState(''); const [error, setError] = useState('');
  useEffect(() => {
    let active = true; let objectUrl = '';
    void agentApi.imageBlob(id).then(blob => { if (active) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); } }).catch(() => { if (active) setError('agent.imageFailed'); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [id]);
  return <><AgentError value={error} />{url && <img src={url} alt={t('agent.generatedImage')} />}</>;
}
