import type { ReactNode } from 'react';
import { cn } from '../../lib/utils';

export type FieldProps = {
  label: ReactNode;
  children: ReactNode;
  className?: string;
};

export function Field({ label, children, className }: FieldProps) {
  return <label className={cn('grid gap-2 text-sm font-extrabold text-foreground', className)}><span className="locale-copy leading-[var(--locale-control-line-height)]">{label}</span>{children}</label>;
}
