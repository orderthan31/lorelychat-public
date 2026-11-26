import { Drawer as AstryxDrawer, SideNav, SideNavItem } from '@astryxdesign/core';
import { WebpImage } from '../atoms/WebpImage';
import { BarChart3, Command, Cpu, Globe2, Home, MessageSquare, Moon, Server, Settings, SlidersHorizontal, Sun, UserRound, X } from 'lucide-react';
import type { RoutePage } from '../../types/domain';
import { lorelyHorizontalLogoUrlForTheme } from '../../utils/assets';
import { useUiStore } from '../../stores/uiStore';
import { Button } from '../atoms';
import { useI18n } from '../../i18n/I18nProvider';
import { MANAGEMENT_NAV_ITEMS, isManagementRoute } from '../../router/managementNavigation';

export type DrawerRoute = {
  page: RoutePage | string;
};

export type DrawerProps = {
  route: DrawerRoute;
  open: boolean;
  navigate: (path: string) => void;
  close: () => void;
  backendOnline: boolean;
};

function BackendHealth({ online }: { online: boolean }) {
  const { t } = useI18n();
  return <div className={`server-health drawer-server-health inline-flex h-8 w-full min-w-0 items-center justify-center gap-1.5 rounded-full border text-xs font-bold shadow-sm ${online ? 'ok border-border bg-card text-muted-foreground' : 'bad border-danger bg-card text-danger'}`} aria-label={t(online ? '백엔드 서버 정상' : '백엔드 서버 오류')}>
    <span className={`server-health-dot h-2 w-2 rounded-full ${online ? 'bg-success shadow-[0_0_0_4px_rgba(16,185,129,.13)]' : 'bg-danger shadow-[0_0_0_4px_rgba(220,38,38,.14)]'}`} aria-hidden="true" />
    <span className="server-health-copy inline-flex items-center gap-1"><Server size={13} aria-hidden="true" />{t(online ? '서버 정상' : '서버 오류')}</span>
  </div>;
}

function ThemeToggle() {
  const theme = useUiStore((state) => state.theme);
  const toggleTheme = useUiStore((state) => state.toggleTheme);
  const isDark = theme === 'dark';
  const { t } = useI18n();
  return <Button type="button" variant="ghost" size="icon" className="theme-toggle drawer-theme-toggle shrink-0 border-border bg-card text-foreground shadow-sm hover:border-primary hover:bg-card hover:text-primary" aria-label={t(isDark ? '라이트 모드로 전환' : '다크 모드로 전환')} title={t(isDark ? '라이트 모드' : '다크 모드')} onClick={toggleTheme}>{isDark ? <Sun size={16} /> : <Moon size={16} />}</Button>;
}

const navItems = [
  { page: 'home', path: '/', label: '홈', icon: Home },
  { page: 'conversations', path: '/conversations', label: '대화방', icon: MessageSquare },
  { page: 'worldSettings', path: '/world-settings', label: '세계관', icon: Globe2 },
  { page: 'characters', path: '/characters', label: '캐릭터', icon: UserRound },
  ...MANAGEMENT_NAV_ITEMS.map(item => ({ ...item, icon: item.page === 'chatCommands' ? Command : item.page === 'presets' ? SlidersHorizontal : Cpu })),
  { page: 'statistics', path: '/statistics', label: '통계', icon: BarChart3 },
  { page: 'settings', path: '/settings', label: '설정', icon: Settings },
] as const;

export function Drawer({ route, open, navigate, close, backendOnline }: DrawerProps) {
  const theme = useUiStore((state) => state.theme);
  const { t } = useI18n();
  const active = (page: string) => route.page === page || isManagementRoute(route.page, page) || (page === 'characters' && route.page.startsWith('character')) || (page === 'conversations' && route.page.startsWith('conversation')) || (page === 'worldSettings' && route.page.startsWith('worldSetting'));
  const go = (path: string) => { navigate(path); close(); };
  return <AstryxDrawer isOpen={open} onOpenChange={next => { if (!next) close(); }} label={t('주요 메뉴')} side="start" width={288} padding={0}>
    <SideNav className="lore-side-nav" aria-label={t('주요 메뉴')} data-modernized="메뉴 드로어 design-system primitive marker"
      header={<div className="drawer-head flex items-center justify-between gap-2.5">
        <div className="drawer-brand min-w-0 flex-1"><WebpImage className="brand-logo-horizontal" src={lorelyHorizontalLogoUrlForTheme(theme)} alt="Lorely Chat" /></div>
        <Button type="button" variant="ghost" size="icon" className="drawer-close" data-autofocus="true" onClick={close} aria-label={t('메뉴 닫기')}><X size={18} /></Button>
      </div>}
      footer={<div className="drawer-footer border-t border-border pt-2">
        <div className="drawer-status-controls flex items-center gap-2" aria-label={t('앱 상태와 테마')}>
          <ThemeToggle />
          <BackendHealth online={backendOnline} />
        </div>
      </div>}>
      {navItems.filter(item => route.page === 'home' || !MANAGEMENT_NAV_ITEMS.some(management => management.path === item.path)).map(({ page, path, label, icon: Icon }) => <SideNavItem key={path} className="lore-side-nav-item" href={path} isSelected={active(page)} label={t(label)} icon={<Icon size={17} aria-hidden="true" />} onClick={event => {
        if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault(); go(path);
      }} />)}
    </SideNav>
  </AstryxDrawer>;
}
