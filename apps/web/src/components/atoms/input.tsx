import * as React from 'react';
import { cn } from '../../lib/utils';

export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn('min-h-11 rounded-xl border border-border bg-card px-3 py-2.5 text-sm text-foreground shadow-inner shadow-foreground/5 outline-none transition placeholder:text-muted-foreground hover:border-primary focus:border-primary focus:bg-card focus:ring-2 focus:ring-primary/25 disabled:cursor-not-allowed disabled:opacity-60', className)} {...props} />;
}
