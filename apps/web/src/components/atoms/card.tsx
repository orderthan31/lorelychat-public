import * as React from 'react';
import { cn } from '../../lib/utils';

// Flat secondary surface. Page shells stay borderless so Card is never nested inside another decorative card.
export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('relative overflow-hidden rounded-xl border border-border bg-card p-4 shadow-[0_1px_2px_rgba(15,23,42,.06)]', className)} data-design-system="flat card surface" {...props} />;
}

export function CardHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('relative z-10 mb-3 grid gap-1.5', className)} {...props} />;
}

export function CardTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h2 className={cn('locale-card-title m-0 font-black tracking-tight text-foreground', className)} {...props} />;
}

export function CardDescription({ className, ...props }: React.HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn('locale-copy m-0 max-w-2xl text-sm leading-relaxed text-muted', className)} {...props} />;
}

export function CardContent({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('relative z-10 grid gap-3', className)} {...props} />;
}
