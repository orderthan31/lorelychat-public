import { useLayoutEffect, useRef, type ReactNode } from 'react';
import './EditorActionBar.scss';

/** One action owner, in normal flow: never clones a submit button or overlays a keyboard. */
export function EditorActionBar({ children, label }: { children: ReactNode; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const bar = ref.current;
    if (!bar) return;
    const shellHeader = document.querySelector<HTMLElement>('.app-bar');
    const measure = () => {
      // A drawer/panel scrollport owns its own sticky origin, not the app header.
      let parent = bar.parentElement;
      let localScrollport = false;
      while (parent && parent !== document.body && parent !== document.documentElement) {
        if (/(auto|scroll|hidden)/.test(getComputedStyle(parent).overflowY)) { localScrollport = true; break; }
        parent = parent.parentElement;
      }
      const offset = !localScrollport && shellHeader ? shellHeader.getBoundingClientRect().height : 0;
      bar.style.setProperty('--editor-action-top', `${offset}px`);
      bar.closest<HTMLElement>('.editor-surface')?.style.setProperty('--editor-scroll-margin', `${offset + bar.getBoundingClientRect().height + 16}px`);
    };
    measure();
    const observer = new ResizeObserver(measure);
    if (shellHeader) observer.observe(shellHeader);
    observer.observe(bar);
    window.addEventListener('resize', measure);
    return () => { observer.disconnect(); window.removeEventListener('resize', measure); };
  }, []);
  return <div ref={ref} className="editor-action-bar" role="group" aria-label={label}>{children}</div>;
}
