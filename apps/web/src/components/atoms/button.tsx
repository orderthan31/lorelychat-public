import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cn } from '../../lib/utils';

export type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  asChild?: boolean;
  variant?: 'default' | 'secondary' | 'ghost' | 'destructive';
  size?: 'default' | 'sm' | 'icon';
};

// design-system product button hierarchy: default=primary CTA, secondary=supportive action, ghost=quiet chrome, destructive=danger action.
const buttonVariants = {
  // Darken only filled control surfaces; preserve the product's global brand tokens.
  default: 'border border-border bg-[color-mix(in_srgb,var(--primary)_75%,#000)] text-primary-foreground shadow-sm hover:bg-[color-mix(in_srgb,var(--primary)_68%,#000)]',
  secondary: 'border border-border bg-surface-muted text-foreground hover:bg-card',
  ghost: 'border border-border bg-card text-muted hover:bg-surface-muted hover:text-foreground',
  destructive: 'border border-border bg-[color-mix(in_srgb,var(--danger)_82%,#000)] text-primary-foreground shadow-sm hover:bg-[color-mix(in_srgb,var(--danger)_75%,#000)]',
} as const;

const buttonSizes = {
  default: 'min-h-11 px-4 py-2.5 text-sm',
  sm: 'min-h-9 px-3.5 py-2 text-xs',
  icon: 'size-11 min-h-11 min-w-11 p-0 aspect-square',
} as const;

export function Button({ asChild = false, variant = 'default', size = 'default', className, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : 'button';
  return <Comp className={cn('inline-flex shrink-0 items-center justify-center gap-1.5 rounded-lg font-semibold leading-none tracking-[-0.01em] transition duration-150 hover:-translate-y-px disabled:pointer-events-none disabled:translate-y-0 disabled:opacity-55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-foreground focus-visible:ring-offset-2 focus-visible:ring-offset-background', buttonVariants[variant], buttonSizes[size], className)} {...props} />;
}
