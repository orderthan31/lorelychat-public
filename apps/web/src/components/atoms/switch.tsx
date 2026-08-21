import * as React from 'react';
import { cn } from '../../lib/utils';

export type SwitchProps = Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type'> & {
  label?: React.ReactNode;
  description?: React.ReactNode;
  compact?: boolean;
};

export function Switch({ label, description, compact = false, className, checked, ...props }: SwitchProps) {
  return <label className={cn('flex min-h-11 cursor-pointer items-center gap-3 py-2 has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60', compact && 'inline-flex justify-end p-0', className)}>
    <input type="checkbox" className="peer sr-only" checked={checked} {...props} />
    <span className="relative h-7 w-12 shrink-0 rounded-full border border-border bg-surface-muted transition peer-focus-visible:ring-2 peer-focus-visible:ring-primary/45 peer-checked:border-primary peer-checked:bg-primary">
      <span className="absolute left-1 top-1 size-5 rounded-full bg-white shadow-sm transition peer-checked:translate-x-5" />
    </span>
    {(label || description) && <span className="locale-copy grid min-w-0 gap-0.5 text-sm leading-[var(--locale-control-line-height)]"><strong className="overflow-wrap-anywhere">{label}</strong>{description && <small className="text-muted">{description}</small>}</span>}
  </label>;
}
