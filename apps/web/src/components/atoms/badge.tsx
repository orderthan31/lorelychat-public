import * as React from 'react';
import { cn } from '../../lib/utils';

export type BadgeProps = React.HTMLAttributes<HTMLSpanElement> & {
  variant?: 'default' | 'secondary' | 'destructive' | 'outline';
};

const badgeVariants = {
  default: 'border-primary bg-card text-primary shadow-sm shadow-primary/10',
  secondary: 'border-accent bg-card text-accent',
  destructive: 'border-danger bg-card text-danger',
  outline: 'border-border bg-card text-muted-foreground',
} as const;

export function Badge({ className, variant = 'secondary', ...props }: BadgeProps) {
  return <span className={cn('inline-flex w-fit items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-extrabold leading-none tracking-tight', badgeVariants[variant], className)} {...props} />;
}
