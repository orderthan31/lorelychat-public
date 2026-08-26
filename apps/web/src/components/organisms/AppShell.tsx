import { useEffect, type ReactNode } from 'react';
import { Menu } from 'lucide-react';
import { Drawer } from './Drawer';
import { pageTitle, type LegacyRoute } from '../../router/legacyRouter';
import { useUiStore } from '../../stores/uiStore';
import { Button } from '../atoms';
import { useI18n } from '../../i18n/I18nProvider';

export type AppShellProps = {
  route: LegacyRoute;
  backendOnline: boolean;
  drawerOpen: boolean;
  setDrawerOpen: (open: boolean) => void;
  navigate: (path: string) => void;
  children: ReactNode;
  overlays?: ReactNode;
  headerActions?: ReactNode;
};

export function AppShell({ route, backendOnline, drawerOpen, setDrawerOpen, navigate, children, overlays, headerActions }: AppShellProps) {
  const isChatRoute = route.page === 'conversationDetail';
  const theme = useUiStore((state) => state.theme);
  const { t } = useI18n();

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle('theme-dark', theme === 'dark');
    root.classList.toggle('theme-light', theme !== 'dark');
    root.dataset.theme = theme;
  }, [theme]);

  return <>
    {!isChatRoute && <header className="app-bar sticky top-0 z-30 flex min-h-[58px] w-full items-center gap-2 overflow-hidden border-b border-border bg-card px-3.5 py-2 text-foreground shadow-lg shadow-primary/5 backdrop-blur-xl md:gap-3 md:px-5" data-modernized="앱 셸 design-system primitive marker">
      <Button type="button" variant="ghost" size="icon" className="inline-grid size-11 min-h-11 shrink-0 place-items-center self-center rounded-xl text-foreground" aria-label={t('메뉴 열기')} onClick={() => setDrawerOpen(true)}><Menu className="block" size={20} /></Button>
      <div className="app-title flex min-h-10 min-w-0 flex-1 items-center self-center overflow-hidden">
        <h1 className="m-0 min-w-0 overflow-hidden text-ellipsis whitespace-nowrap text-[16px] leading-[1.2]"><strong className="font-extrabold text-foreground">{t(pageTitle(route))}</strong></h1>
      </div>
      {headerActions && <div className="app-actions ml-auto flex shrink-0 items-center justify-end gap-2 overflow-x-auto" aria-label={t('페이지 액션')}>{headerActions}</div>}
    </header>}
    <Drawer route={route} open={drawerOpen} navigate={navigate} close={() => setDrawerOpen(false)} backendOnline={backendOnline} />
    <main className="shell">{children}</main>
    {overlays}
  </>;
}
