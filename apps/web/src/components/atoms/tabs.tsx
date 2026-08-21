import * as React from 'react';
import * as TabsPrimitive from '@radix-ui/react-tabs';
import { cn } from '../../lib/utils';

export const Tabs = TabsPrimitive.Root;

export const TabsList = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(({ className, ...props }, ref) => <TabsPrimitive.List
  ref={ref}
  className={cn('inline-flex min-h-11 items-center gap-1 rounded-2xl border border-border bg-card/80 p-1 text-muted-foreground', className)}
  {...props}
/>);
TabsList.displayName = TabsPrimitive.List.displayName;

export const TabsTrigger = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(({ className, ...props }, ref) => <TabsPrimitive.Trigger
  ref={ref}
  className={cn('locale-control-label inline-flex min-h-11 min-w-max flex-1 items-center justify-center gap-2 rounded-xl px-3 py-2 text-center font-extrabold leading-[var(--locale-control-line-height)] text-muted-foreground outline-none transition-colors focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50 data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-sm', className)}
  {...props}
/>);
TabsTrigger.displayName = TabsPrimitive.Trigger.displayName;

export const TabsContent = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, ...props }, ref) => <TabsPrimitive.Content
  ref={ref}
  className={cn('min-w-0 outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background', className)}
  {...props}
/>);
TabsContent.displayName = TabsPrimitive.Content.displayName;
