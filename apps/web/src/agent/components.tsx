import { useEffect, type ReactNode } from 'react';
import { X } from 'lucide-react';
import { AgentMark } from './AgentMark';
import { useI18n } from '../i18n/I18nProvider';
import { AGENT_DOCK_QUERY, agentActions, useAgentStore } from './store';
import { AgentPanel } from './panel';
import './agent.css';

export function AgentProvider({ children }: { children: ReactNode }) {
  useEffect(() => { void agentActions.loadSettings(); }, []);
  return <>{children}</>;
}
export { AgentError } from './AgentError';
let launcherElement: HTMLButtonElement | null = null;
function openAgentFrom(element: HTMLButtonElement, open: boolean) {
  launcherElement = element;
  useAgentStore.getState().setOpen(open);
}
export function AgentOpenButton() {
  const { t } = useI18n();
  const enabled = useAgentStore(s => s.settings?.enabled);
  const open = useAgentStore(s => s.open);
  if (!enabled) return null;
  return <button type="button" aria-expanded={open} onClick={event => openAgentFrom(event.currentTarget, true)}>{t('agent.open')}</button>;
}
export function AgentLauncher() {
  const { t } = useI18n();
  const enabled = useAgentStore(s => s.settings?.enabled);
  const open = useAgentStore(s => s.open);
  if (!enabled) return null;
  return <button type="button" className="agent-launcher" aria-label={t('agent.title')} title={t('agent.title')} aria-expanded={open} onClick={event => openAgentFrom(event.currentTarget, !open)}><AgentMark size={30} active={open} /></button>;
}
export function AgentSurface() {
  const { t } = useI18n();
  const enabled = useAgentStore(s => !!s.settings?.enabled);
  const open = useAgentStore(s => s.open && !!s.settings?.enabled);
  const mode = useAgentStore(s => s.surfaceMode);

  useEffect(() => {
    const media = window.matchMedia(AGENT_DOCK_QUERY);
    const update = () => useAgentStore.setState({ surfaceMode: media.matches ? 'dock' : 'sheet' });
    media.addEventListener('change', update); update();
    return () => media.removeEventListener('change', update);
  }, []);
  useEffect(() => { if (open) void agentActions.refresh(); }, [open]);
  const close = () => {
    useAgentStore.getState().setOpen(false);
    const opener = launcherElement?.isConnected ? launcherElement : document.querySelector<HTMLButtonElement>('.agent-launcher');
    opener?.focus({ preventScroll: true });
  };
  useEffect(() => {
    if (!open) return;
    // No focus trap/dismissable layer: page controls and other dialogs keep
    // their own focus and Escape behavior. Closed surfaces install no handler.
    const onEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented || event.isComposing) return;
      const otherDialog = Array.from(document.querySelectorAll('[role="dialog"]:not(.agent-surface), [role="alertdialog"]')).some(node => node.getClientRects().length > 0);
      if (otherDialog) return;
      event.preventDefault(); close();
    };
    document.addEventListener('keydown', onEscape);
    return () => document.removeEventListener('keydown', onEscape);
  }, [open]);
  // One DOM owner throughout resize/open/close; no Portal/branch remount can
  // interrupt an IME composition, textarea selection or transcript reading.
  return <aside className={`agent-surface agent-ui ${mode === 'dock' ? 'agent-dock' : 'agent-modal'}`} data-surface={mode} hidden={!open} role={mode === 'sheet' ? 'dialog' : 'complementary'} aria-label={t('agent.title')} aria-modal={mode === 'sheet' ? false : undefined}>
    <header className="agent-panel-header"><h2><AgentMark size={28} />{t('agent.title')}</h2><button type="button" aria-label={t('닫기')} onClick={close}><X size={20} /></button></header>
    {enabled && <AgentPanel />}
  </aside>;
}
