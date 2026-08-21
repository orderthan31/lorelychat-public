import * as React from 'react';
import { cn } from '../../lib/utils';

export type SelectProps = React.SelectHTMLAttributes<HTMLSelectElement>;

export function Select({ className, children, ...props }: SelectProps) {
  return <select className={cn('min-h-11 w-full rounded-xl border border-border bg-card px-3 py-2.5 text-sm font-semibold text-foreground shadow-inner shadow-foreground/5 outline-none transition hover:border-primary focus:border-primary focus:bg-card focus:ring-2 focus:ring-primary/25 disabled:cursor-not-allowed disabled:opacity-60', className)} {...props}>{children}</select>;
}
