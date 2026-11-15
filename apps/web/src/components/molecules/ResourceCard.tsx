import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { Button, type ButtonProps } from '../atoms';
import { cn } from '../../lib/utils';
import { PanelRightOpen } from 'lucide-react';

export type ResourceCardProps = {
  variant?: 'row' | 'media';
  descriptionLines?: 1 | 2;
  title: ReactNode;
  description?: ReactNode;
  meta?: ReactNode;
  media?: ReactNode;
  actions?: ReactNode;
  onOpen: () => void;
  onPreview?: () => void;
  previewLabel?: string;
  previewPlacement?: 'footer' | 'actions';
  selected?: boolean;
  openLabel?: string;
  className?: string;
};

export function ResourceCard({
  variant = 'row',
  descriptionLines = 2,
  title,
  description,
  meta,
  media,
  actions,
  onOpen,
  onPreview,
  previewLabel,
  previewPlacement = 'footer',
  selected = false,
  openLabel,
  className,
}: ResourceCardProps) {
  const isMedia = variant === 'media';
  const inlinePreview = !!onPreview && previewPlacement === 'actions';

  return <article
    className={cn(
      'group relative h-full min-w-0 overflow-hidden rounded-xl border border-border bg-card text-foreground shadow-[0_1px_2px_rgba(15,23,42,.06)] transition-colors hover:border-primary/45',
      className,
      onPreview && !inlinePreview && 'resource-card-with-preview',
    )}
    data-resource-card={variant}
    data-selected={selected || undefined}
  >
    <button
      type="button"
      className={cn(
        'grid h-full min-h-0 w-full min-w-0 whitespace-normal text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary/45',
        isMedia ? 'grid-rows-[auto_1fr] gap-0' : media ? 'grid-cols-[56px_minmax(0,1fr)] items-center gap-2.5 p-2.5' : 'grid-cols-1 p-2.5',
      )}
      aria-label={openLabel}
      onClick={onOpen}
    >
      {media && <span
        className={cn(
          'block overflow-hidden border border-border bg-surface-muted',
          isMedia ? 'aspect-[12/5] w-full border-x-0 border-t-0 sm:aspect-[16/9]' : 'aspect-square w-14 rounded-lg',
        )}
        data-resource-media
      >{media}</span>}
      <span className={cn('grid min-w-0 content-start gap-1', isMedia ? 'p-2.5' : '')}>
        <strong className="line-clamp-1 min-w-0 text-[15px] font-bold leading-5 tracking-[-0.02em] text-foreground">{title}</strong>
        {description && <span className={cn(descriptionLines === 1 ? 'line-clamp-1' : 'line-clamp-2', 'min-w-0 text-sm leading-5 text-muted-foreground')}>{description}</span>}
        {meta && <span className="mt-auto flex w-full min-w-0 items-center justify-between gap-3 overflow-hidden pt-0.5 text-xs leading-4 text-muted-foreground">{meta}</span>}
      </span>
    </button>
    {onPreview && !inlinePreview && <button type="button" className="resource-preview-toggle" data-resource-preview aria-pressed={selected} onClick={onPreview}>{previewLabel}</button>}
    {(actions || inlinePreview) && <div className="absolute right-2 top-2 z-10 flex items-center gap-1" data-resource-actions data-resource-actions-inline={inlinePreview || undefined}>
      {actions}
      {inlinePreview && <button type="button" className="resource-preview-action" data-resource-preview aria-label={previewLabel} title={previewLabel} aria-pressed={selected} onClick={onPreview}><PanelRightOpen size={16} aria-hidden="true" /></button>}
    </div>}
  </article>;
}

export type ResourceCardActionProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'aria-label'> & {
  label: string;
  variant?: Extract<ButtonProps['variant'], 'ghost' | 'destructive'>;
};

export function ResourceCardAction({ label, variant = 'ghost', className, children, ...props }: ResourceCardActionProps) {
  return <Button
    type="button"
    size="icon"
    variant={variant}
    aria-label={label}
    title={label}
    className={cn(
      'border-transparent bg-transparent text-muted-foreground shadow-none hover:translate-y-0 hover:border-transparent hover:bg-surface-muted hover:text-foreground',
      variant === 'destructive' && 'border-transparent bg-transparent text-muted-foreground hover:border-transparent hover:bg-surface-muted hover:text-danger',
      className,
    )}
    {...props}
  >{children}</Button>;
}
