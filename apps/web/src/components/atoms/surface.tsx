import type * as React from 'react';
import { cn } from '../../lib/utils';

export type SurfaceVariant = 'panel' | 'subtle' | 'raised' | 'inset' | 'note' | 'danger' | 'media' | 'mediaSquare';
export type SurfaceTone = 'default' | 'primary' | 'accent' | 'success' | 'warning' | 'danger' | 'muted';

export type SurfaceProps = React.HTMLAttributes<HTMLDivElement> & {
  variant?: SurfaceVariant;
  tone?: SurfaceTone;
};

const surfaceVariants: Record<SurfaceVariant, string> = {
  panel: 'border border-border bg-card text-card-foreground shadow-sm shadow-foreground/5',
  subtle: 'border border-border/75 bg-muted/10 text-foreground',
  raised: 'border border-border bg-card text-card-foreground shadow-xl shadow-foreground/10',
  inset: 'border border-border bg-background text-foreground shadow-inner shadow-foreground/5',
  note: 'border border-warning/35 bg-warning/10 text-foreground',
  danger: 'border border-danger bg-card text-danger',
  media: 'border border-border bg-card text-card-foreground shadow-sm shadow-foreground/10',
  mediaSquare: 'aspect-square border border-border bg-card text-card-foreground shadow-sm shadow-foreground/10',
};

const surfaceTones: Record<SurfaceTone, string> = {
  default: '',
  primary: 'border-primary bg-card',
  accent: 'border-accent bg-card',
  success: 'border-success bg-card',
  warning: 'border-warning bg-card',
  danger: 'border-danger bg-card text-danger',
  muted: 'border-border bg-card text-muted-foreground',
};

export function Surface({ variant = 'panel', tone = 'default', className, ...props }: SurfaceProps) {
  return <div className={cn(variant === 'mediaSquare' ? 'rounded-none' : 'rounded-2xl', surfaceVariants[variant], surfaceTones[tone], className)} {...props} />;
}

export type TextTone = 'default' | 'muted' | 'primary' | 'accent' | 'success' | 'warning' | 'danger' | 'inverse';

const textTones: Record<TextTone, string> = {
  default: 'text-foreground',
  muted: 'text-muted',
  primary: 'text-primary',
  accent: 'text-accent',
  success: 'text-success',
  warning: 'text-warning',
  danger: 'text-danger',
  inverse: 'text-primary-foreground',
};

export type TextProps = React.HTMLAttributes<HTMLSpanElement> & {
  as?: 'span' | 'p' | 'small' | 'strong' | 'div';
  tone?: TextTone;
};

export function Text({ as: Comp = 'span', tone = 'default', className, ...props }: TextProps) {
  return <Comp className={cn(textTones[tone], className)} {...props} />;
}
