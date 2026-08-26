import { BarChart3, Command, Globe2, Home, MessageSquare, Moon, Server, Settings, Sun, UserRound, X } from 'lucide-react';
import type { RoutePage } from '../../types/domain';
import { lorelyHorizontalLogoUrlForTheme } from '../../utils/assets';
import { useUiStore } from '../../stores/uiStore';
import { Button } from '../atoms';
import { useI18n } from '../../i18n/I18nProvider';

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
  { page: 'chatCommands', path: '/chat-commands', label: '커맨드', icon: Command },
  { page: 'statistics', path: '/statistics', label: '통계', icon: BarChart3 },
  { page: 'settings', path: '/settings', label: '설정', icon: Settings },
] as const;

export function Drawer({ route, open, navigate, close, backendOnline }: DrawerProps) {
  const theme = useUiStore((state) => state.theme);
  const { t } = useI18n();
  const active = (page: string) => route.page === page || (page === 'characters' && route.page.startsWith('character')) || (page === 'conversations' && route.page.startsWith('conversation')) || (page === 'worldSettings' && route.page.startsWith('worldSetting'));
  const go = (path: string) => { navigate(path); close(); };
  if (!open) return null;
  return <>
    <button type="button" aria-label={t('메뉴 닫기')} className="drawer-backdrop fixed inset-0 z-40 min-h-screen w-full rounded-none bg-foreground/30 p-0" onClick={close}></button>
    <aside className="side-drawer open fixed left-0 top-0 z-50 grid h-screen w-[min(82vw,288px)] translate-x-0 grid-rows-[auto_1fr_auto] gap-4 border-r border-border bg-card p-3.5 text-foreground shadow-2xl shadow-foreground/20 backdrop-blur-xl transition-transform duration-150" data-modernized="메뉴 드로어 design-system primitive marker">
      <div className="drawer-head flex items-center justify-between gap-2.5">
        <div className="drawer-brand min-w-0 flex-1"><img className="brand-logo-horizontal" src={lorelyHorizontalLogoUrlForTheme(theme)} alt="Lorely Chat" /></div>
        <Button type="button" variant="ghost" size="icon" className="drawer-close" onClick={close} aria-label={t('메뉴 닫기')}><X size={18} /></Button>
      </div>
      <nav className="drawer-nav grid content-start gap-1.5" aria-label={t('주요 메뉴')}>
        {navItems.map(({ page, path, label, icon: Icon }) => <Button key={path} type="button" variant={active(page) ? 'secondary' : 'ghost'} className={`w-full justify-self-stretch justify-start text-left min-h-11 px-3.5 py-2.5 ${active(page) ? 'active border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-foreground'}`} onClick={() => go(path)}><Icon size={17} />{t(label)}</Button>)}
      </nav>
      <div className="drawer-footer grid gap-2 border-t border-border pt-3" aria-label={t('앱 상태와 유저 프로필')}>
        <div className="drawer-status-controls flex items-center gap-2" aria-label={t('앱 상태와 테마')}>
          <ThemeToggle />
          <BackendHealth online={backendOnline} />
        </div>
        <div className="grid grid-cols-[42px_minmax(0,1fr)] items-center gap-2.5 rounded-2xl border border-border bg-[color-mix(in_srgb,var(--card)_92%,var(--primary)_8%)] p-2.5 text-foreground" aria-label={t('유저 프로필 목업')}>
          <span className="inline-grid size-[42px] place-items-center rounded-full bg-[linear-gradient(135deg,var(--primary),var(--accent-2))] text-primary-foreground shadow-[0_10px_22px_color-mix(in_srgb,var(--primary)_24%,transparent)]"><UserRound size={18} /></span>
          <span className="grid min-w-0 gap-0.5"><strong className="truncate text-[13px]">{t('게스트 유저')}</strong><small className="truncate text-[11px] font-semibold text-muted">{t('로컬 프로필 · 로그인 준비중')}</small></span>
        </div>
      </div>
    </aside>
  </>;
}
