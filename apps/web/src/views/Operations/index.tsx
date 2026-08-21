import { Button } from '../../components/atoms';
import { useI18n } from '../../i18n/I18nProvider';

export type OperationsViewProps = {
  navigate: (path: string) => void;
};

export function OperationsView({ navigate }: OperationsViewProps) {
  const { t } = useI18n();
  const links = [['/uiux', 'UI/UX 가이드']] as const;

  return <section className="panel page operations-hub grid gap-3" data-page="operations-hub">
    <h1 className="m-0 text-xl font-bold text-foreground">{t('운영')}</h1>
    <div className="divide-y divide-border border-y border-border">
      {links.map(([path, label]) => <Button key={path} type="button" variant="ghost" className="operations-hub-link h-auto w-full justify-start rounded-none px-2 py-3 text-left" onClick={() => navigate(path)}>{t(label)}</Button>)}
    </div>
  </section>;
}
