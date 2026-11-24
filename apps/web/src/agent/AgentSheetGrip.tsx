import { useLayoutEffect, useRef, useState, type PointerEvent } from 'react';
import { useI18n } from '../i18n/I18nProvider';

// Only this control comes and goes. The surface and its editor never move.
export function AgentSheetGrip() {
  const { t } = useI18n();
  const button = useRef<HTMLButtonElement>(null);
  const drag = useRef<{ id: number; y: number; height: number; moved: boolean } | null>(null);
  const suppressClick = useRef(false);
  const [expanded, setExpanded] = useState(false);
  useLayoutEffect(() => {
    const handle = button.current!;
    const sheet = handle.closest<HTMLElement>('.agent-modal')!;
    const viewport = window.visualViewport;
    const sync = () => {
      const active = drag.current;
      drag.current = null;
      if (active) {
        suppressClick.current = true;
        if (handle.hasPointerCapture(active.id)) handle.releasePointerCapture(active.id);
      }
      sheet.style.removeProperty('--agent-drag-height');
      const height = viewport?.height ?? window.innerHeight;
      sheet.style.setProperty('--agent-available', `${height}px`);
      sheet.style.setProperty('--agent-bottom', `${Math.max(0, window.innerHeight - height - (viewport?.offsetTop ?? 0))}px`);
    };
    sync();
    window.addEventListener('resize', sync);
    viewport?.addEventListener('resize', sync);
    viewport?.addEventListener('scroll', sync);
    return () => {
      window.removeEventListener('resize', sync);
      viewport?.removeEventListener('resize', sync);
      viewport?.removeEventListener('scroll', sync);
      for (const prop of ['--agent-available', '--agent-bottom', '--agent-drag-height']) sheet.style.removeProperty(prop);
    };
  }, []);

  function finish(event: PointerEvent<HTMLButtonElement>, commit: boolean) {
    const active = drag.current;
    if (!active || active.id !== event.pointerId) return;
    drag.current = null;
    const dy = active.y - event.clientY;
    if (active.moved || Math.abs(dy) >= 6 || !commit) {
      suppressClick.current = true;
      if (commit && Math.abs(dy) >= 24) setExpanded(dy > 0);
    }
    const handle = event.currentTarget;
    handle.closest<HTMLElement>('.agent-modal')?.style.removeProperty('--agent-drag-height');
    if (handle.hasPointerCapture(active.id)) handle.releasePointerCapture(active.id);
  }

  const label = t(expanded ? 'agent.collapseSheet' : 'agent.expandSheet');
  return <button type="button" className="agent-sheet-grip" ref={button} aria-label={label} title={label} aria-expanded={expanded}
    onPointerDown={event => {
      if (!event.isPrimary || event.button !== 0 || drag.current) return;
      event.preventDefault(); // Keep the composer selection/IME owner during touch or mouse drag.
      const handle = event.currentTarget;
      suppressClick.current = false;
      drag.current = { id: event.pointerId, y: event.clientY, height: handle.closest('.agent-modal')!.getBoundingClientRect().height, moved: false };
      handle.setPointerCapture(event.pointerId);
    }}
    onPointerMove={event => {
      const active = drag.current;
      if (!active || active.id !== event.pointerId) return;
      const dy = active.y - event.clientY;
      if (Math.abs(dy) < 6 && !active.moved) return;
      active.moved = true;
      suppressClick.current = true;
      event.currentTarget.closest<HTMLElement>('.agent-modal')?.style.setProperty('--agent-drag-height', `${active.height + dy}px`);
    }}
    onPointerUp={event => finish(event, true)} onPointerCancel={event => finish(event, false)} onLostPointerCapture={event => finish(event, false)}
    onClick={event => {
      if (event.detail === 0 || !suppressClick.current) setExpanded(value => !value);
    }} />;
}
