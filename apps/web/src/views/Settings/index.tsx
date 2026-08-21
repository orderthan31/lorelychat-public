import { Cpu, Languages, NotebookTabs } from 'lucide-react';
import { AnimatedSettingsMark, Select } from '../../components/atoms';
import { SettingsNavItem } from '../../components/molecules';
import { useI18n } from '../../i18n/I18nProvider';
import type { AppLocale, UiMessageKey } from '../../i18n/core';

export type SettingsViewProps = {
  navigate: (path: string) => void;
};

export function SettingsView({ navigate }: SettingsViewProps) {
  const { locale, setLocale, t } = useI18n();
  const links: ReadonlyArray<{
    path: string;
    label: UiMessageKey;
    icon: typeof Cpu;
  }> = [
    { path: '/model-settings', label: '모델 설정', icon: Cpu },
    { path: '/presets', label: '프리셋', icon: NotebookTabs },
  ];

  return <section className="page settings-hub grid gap-4" data-page="settings-hub">
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-4">
      <div className="flex min-w-0 items-center gap-3">
        <AnimatedSettingsMark className="size-12 shrink-0" label={t('설정')} />
        <h1 className="text-xl font-bold tracking-tight text-foreground">{t('설정')}</h1>
      </div>
      <label className="flex min-w-[180px] items-center gap-2">
        <Languages className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <span className="sr-only">{t('표시 언어')}</span>
        <Select aria-label={t('표시 언어')} value={locale} onChange={(event) => setLocale(event.target.value as AppLocale)}>
          <option value="ko">한국어</option>
          <option value="en">English</option>
          <option value="ja">日本語</option>
        </Select>
      </label>
    </header>

    <div className="divide-y divide-border border-y border-border">
        {links.map(({ path, label, icon }) => <SettingsNavItem
          key={path}
          icon={icon}
          label={t(label)}
          onClick={() => navigate(path)}
        />)}
    </div>
  </section>;
}
