import { Dialog } from '@astryxdesign/core';
import { useLayoutEffect, useState, type CSSProperties, type ReactNode } from 'react';

export function ExpandedTextDialog({ open, onOpenChange, title, children, actions }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children: ReactNode;
  actions: ReactNode;
}) {
  const [visibleHeight, setVisibleHeight] = useState<number>();
  useLayoutEffect(() => {
    if (!open || !window.visualViewport) return;
    const viewport = window.visualViewport;
    const update = () => setVisibleHeight(viewport.height);
    update();
    viewport.addEventListener('resize', update);
    return () => viewport.removeEventListener('resize', update);
  }, [open]);
  return <Dialog isOpen={open} onOpenChange={onOpenChange} aria-label={title} width={720}
    position={{ top: 'max(8px, env(safe-area-inset-top))' }} maxHeight={visibleHeight ? Math.max(1, visibleHeight - 16) : 'calc(100dvh - 16px)'} padding={3}
    style={visibleHeight ? { '--editor-viewport': `${visibleHeight}px` } as CSSProperties : undefined}>
    <div className="expanded-text-layout">
      <h2>{title}</h2>
      {children}
      <footer className="text-editor-foot">{actions}</footer>
    </div>
  </Dialog>;
}
