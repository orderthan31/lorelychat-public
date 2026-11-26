import { Dialog, LayoutHeader } from '@astryxdesign/core';
import { useLayoutEffect, useState, type CSSProperties, type ReactNode } from 'react';
import { useMediaMatch } from '../../hooks/useMediaMatch';

type ExpandedTextDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children: ReactNode;
} & ({ actions: ReactNode; closeAction?: never } | { actions?: never; closeAction: ReactNode });

export function ExpandedTextDialog({ open, onOpenChange, title, children, actions, closeAction }: ExpandedTextDialogProps) {
  const focusMode = closeAction != null;
  const compact = useMediaMatch('(max-width: 700px), (max-height: 520px)');
  const fullscreen = focusMode && compact;
  const [viewport, setViewport] = useState<{ height: number; top: number }>();
  useLayoutEffect(() => {
    const viewport = window.visualViewport;
    if (!open || !viewport) return;
    const update = () => setViewport({ height: viewport.height, top: viewport.offsetTop });
    update();
    viewport.addEventListener('resize', update);
    viewport.addEventListener('scroll', update);
    return () => {
      viewport.removeEventListener('resize', update);
      viewport.removeEventListener('scroll', update);
    };
  }, [open]);
  return <Dialog isOpen={open} onOpenChange={onOpenChange} aria-label={title} width={720}
    variant={fullscreen ? 'fullscreen' : 'standard'} purpose={focusMode ? 'form' : 'info'}
    className={fullscreen ? 'focus-editor-fullscreen' : undefined}
    position={focusMode ? undefined : { top: 'max(8px, env(safe-area-inset-top))' }}
    maxHeight={viewport ? Math.max(1, viewport.height - 16) : 'calc(100dvh - 16px)'}
    padding={fullscreen ? undefined : 3}
    style={viewport ? { '--editor-viewport': `${viewport.height}px`, '--editor-offset': `${viewport.top}px` } as CSSProperties : undefined}>
    <div className={focusMode ? 'expanded-text-layout focus-editor' : 'expanded-text-layout'}>
      {focusMode ? <LayoutHeader padding={0} hasDivider className="focus-editor-header">
        <div className="focus-editor-toolbar"><h2>{title}</h2>{closeAction}</div>
      </LayoutHeader> : <h2>{title}</h2>}
      {children}
      {!focusMode && <footer className="text-editor-foot">{actions}</footer>}
    </div>
  </Dialog>;
}
