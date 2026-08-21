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
  default: 'border border-primary bg-primary text-primary-foreground shadow-sm shadow-primary/20 hover:bg-primary hover:border-primary',
  secondary: 'border border-primary bg-card text-primary shadow-sm shadow-primary/10 hover:bg-card hover:border-primary',
  ghost: 'border border-border bg-card text-muted-foreground hover:bg-card hover:text-primary hover:border-primary',
  destructive: 'border border-danger bg-danger text-white shadow-sm shadow-destructive/25 hover:bg-danger hover:border-danger',
} as const;

const buttonSizes = {
  default: 'min-h-11 px-4 py-2.5 text-sm',
  sm: 'min-h-9 px-3.5 py-2 text-xs',
  icon: 'size-11 min-h-11 min-w-11 p-0 aspect-square',
} as const;

export function Button({ asChild = false, variant = 'default', size = 'default', className, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : 'button';
  return <Comp className={cn('inline-flex shrink-0 items-center justify-center gap-1.5 rounded-lg font-semibold leading-none tracking-[-0.01em] transition duration-150 hover:-translate-y-px disabled:pointer-events-none disabled:translate-y-0 disabled:opacity-55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/45 focus-visible:ring-offset-2 focus-visible:ring-offset-background', buttonVariants[variant], buttonSizes[size], className)} {...props} />;
}
