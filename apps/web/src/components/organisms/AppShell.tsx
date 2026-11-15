import { useEffect, useState, type ReactNode } from 'react';
import { PageHeaderTarget } from '../molecules/PageHeaderActions';
import { Menu, MoreHorizontal } from 'lucide-react';
import * as Popover from '@radix-ui/react-popover';
import { Drawer } from './Drawer';
import { AgentLauncher, AgentSurface } from '../../agent/components';
import { pageTitle, type LegacyRoute } from '../../router/legacyRouter';
import { useUiStore } from '../../stores/uiStore';
import { Button } from '../atoms';
import { useI18n } from '../../i18n/I18nProvider';
import { MANAGEMENT_NAV_ITEMS, isManagementRoute } from '../../router/managementNavigation';

export type AppShellProps = {
  route: LegacyRoute;
  backendOnline: boolean;
  drawerOpen: boolean;
  setDrawerOpen: (open: boolean) => void;
  navigate: (path: string) => void;
  children: ReactNode;
  overlays?: ReactNode;
  headerActions?: ReactNode;
  secondaryHeaderActions?: boolean;
  chatHeaderReady?: boolean;
};

export function AppShell({ route, backendOnline, drawerOpen, setDrawerOpen, navigate, children, overlays, headerActions, secondaryHeaderActions = false, chatHeaderReady = true }: AppShellProps) {
  const [actionTarget, setActionTarget] = useState<HTMLDivElement | null>(null);
  const isChatRoute = route.page === 'conversationDetail';
  const theme = useUiStore((state) => state.theme);
  const { t } = useI18n();

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle('theme-dark', theme === 'dark');
    root.classList.toggle('theme-light', theme !== 'dark');
    root.dataset.theme = theme;
  }, [theme]);

  return <PageHeaderTarget.Provider value={isChatRoute ? null : actionTarget}>
    {(!isChatRoute || !chatHeaderReady) && <header className="app-bar sticky top-0 z-30 flex min-h-[56px] w-full items-center gap-2 overflow-hidden border-b border-border bg-card px-3.5 py-1.5 text-foreground md:gap-3 md:px-5" data-modernized="앱 셸 design-system primitive marker">
      <Button type="button" variant="ghost" size="icon" className="inline-grid size-11 min-h-11 shrink-0 place-items-center self-center rounded-xl text-foreground" aria-label={t('메뉴 열기')} onClick={() => setDrawerOpen(true)}><Menu className="block" size={20} /></Button>
      <div className="app-title flex min-h-10 min-w-0 flex-1 items-center self-center overflow-hidden">
        <h1 className="m-0 min-w-0 overflow-hidden text-ellipsis whitespace-nowrap text-[16px] leading-[1.2]"><strong className="font-extrabold text-foreground">{t(pageTitle(route))}</strong></h1>
      </div>
      {route.page === 'home' && <nav className="app-tool-nav" aria-label={t('설정')}>
        {MANAGEMENT_NAV_ITEMS.map(item => <a key={item.path} href={item.path} aria-current={isManagementRoute(route.page, item.page) ? 'page' : undefined} onClick={event => {
          if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          event.preventDefault(); navigate(item.path);
        }}>{t(item.label)}</a>)}
      </nav>}
      {headerActions && <div className="app-actions" aria-label={t('페이지 액션')}>{secondaryHeaderActions ? <Popover.Root><Popover.Trigger asChild><Button variant="ghost" size="icon" aria-label={t('페이지 액션')} title={t('페이지 액션')}><MoreHorizontal size={20} /></Button></Popover.Trigger><Popover.Portal><Popover.Content align="end" sideOffset={6} className="header-secondary-menu" aria-label={t('페이지 액션')}>{headerActions}</Popover.Content></Popover.Portal></Popover.Root> : headerActions}</div>}
      <div ref={setActionTarget} className="page-header-slot" />
      <AgentLauncher />
    </header>}
    <Drawer route={route} open={drawerOpen} navigate={navigate} close={() => setDrawerOpen(false)} backendOnline={backendOnline} />
    <div className="agent-layout"><main className="shell">{children}</main><AgentSurface /></div>
    {overlays}
  </PageHeaderTarget.Provider>;
}
