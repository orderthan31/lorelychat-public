import { ChevronRight, type LucideIcon } from 'lucide-react';
import { Button } from '../atoms';

export type SettingsNavItemProps = {
  icon: LucideIcon;
  label: string;
  description?: string;
  meta?: string;
  onClick: () => void;
};

export function SettingsNavItem({ icon: Icon, label, description, meta, onClick }: SettingsNavItemProps) {
  return <Button
    type="button"
    variant="ghost"
    className="group h-auto min-h-16 w-full min-w-0 max-w-full justify-start gap-3 overflow-hidden whitespace-normal rounded-none border-0 bg-transparent px-1 py-2.5 text-left shadow-none hover:translate-y-0 hover:bg-surface-muted focus-visible:ring-2 focus-visible:ring-primary"
    aria-label={label}
    onClick={onClick}
  >
    <span className="inline-grid size-10 shrink-0 place-items-center rounded-lg bg-primary-soft text-primary" aria-hidden="true">
      <Icon size={20} strokeWidth={2.2} />
    </span>
    <span className="grid min-w-0 flex-1 gap-0.5 overflow-hidden">
      <span className="locale-control-label !min-h-0 !whitespace-normal text-left font-black text-foreground">{label}</span>
      {description ? <span className="locale-copy !whitespace-normal break-words text-xs font-semibold leading-relaxed text-muted-foreground">{description}</span> : null}
      {meta ? <span className="text-[11px] font-bold text-primary">{meta}</span> : null}
    </span>
    <ChevronRight className="size-5 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-primary" aria-hidden="true" />
  </Button>;
}
