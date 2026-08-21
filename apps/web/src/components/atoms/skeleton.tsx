import type { HTMLAttributes } from 'react';
import { cn } from '../../lib/utils';

export type SkeletonProps = HTMLAttributes<HTMLSpanElement>;

export function Skeleton({ className, ...props }: SkeletonProps) {
  return <span
    aria-hidden="true"
    className={cn(
      'skeleton-shimmer relative block overflow-hidden rounded-md bg-surface-muted',
      className,
    )}
    {...props}
  />;
}
