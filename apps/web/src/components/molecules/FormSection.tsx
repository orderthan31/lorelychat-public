import type { HTMLAttributes, ReactNode } from 'react';
import { cn } from '../../lib/utils';

export type FormSectionProps = HTMLAttributes<HTMLElement> & {
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
};

export function FormSection({ title, description, action, className, children, ...props }: FormSectionProps) {
  return <section
    data-form-section
    className={cn('grid min-w-0 gap-3 border-t border-border pt-4 first:border-t-0 first:pt-0', className)}
    {...props}
  >
    {title || description || action ? <header className="flex min-w-0 flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 flex-1">
        {title ? <h2 className="m-0 text-base font-bold leading-snug text-foreground">{title}</h2> : null}
        {description ? <p className="m-0 mt-1 text-sm leading-relaxed text-muted-foreground">{description}</p> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </header> : null}
    <div className="grid min-w-0 gap-3">{children}</div>
  </section>;
}
