import { useCallback, useLayoutEffect, useState } from 'react';

export const CONVERSATION_CHAT_MAX_WIDTH = 960;
export const CONVERSATION_PANEL_MIN_WIDTH = 320;
export const CONVERSATION_DOCK_MIN_WIDTH = 960;

/** Cap the reading column; give the inspector the remaining workspace width. */
export function conversationPanelGeometry(width: number) {
  const available = Number.isFinite(width) ? Math.max(0, width) : 0;
  return { docked: available >= CONVERSATION_DOCK_MIN_WIDTH, panelWidth: Math.max(CONVERSATION_PANEL_MIN_WIDTH, available - CONVERSATION_CHAT_MAX_WIDTH) };
}

export type ConversationPanelPreference = { dockOpen?: boolean; modalOpen?: boolean };
export function conversationPanelIsOpen(docked: boolean, preference: ConversationPanelPreference = {}) {
  return docked ? preference.dockOpen !== false : preference.modalOpen === true;
}

/** Keep this owner above the detail view; never key it by a fetched room object. */
export function useConversationPanelLayout(roomId: string) {
  const [element, setElement] = useState<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  const [preferences, setPreferences] = useState<Record<string, ConversationPanelPreference>>({});
  const workspaceRef = useCallback((node: HTMLDivElement | null) => setElement(node), []);
  useLayoutEffect(() => {
    if (!element) return;
    const measure = () => setWidth(element.getBoundingClientRect().width);
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(element);
    window.addEventListener('resize', measure);
    return () => { observer?.disconnect(); window.removeEventListener('resize', measure); };
  }, [element]);
  const { docked, panelWidth } = conversationPanelGeometry(width);
  const open = !!roomId && conversationPanelIsOpen(docked, preferences[roomId]);
  const setOpen = useCallback((value: boolean) => {
    if (!roomId) return;
    setPreferences(current => ({ ...current, [roomId]: {
      ...current[roomId], [docked ? 'dockOpen' : 'modalOpen']: value,
    } }));
  }, [roomId, docked]);
  // A dock becoming narrow must not unexpectedly cover the conversation.
  useLayoutEffect(() => {
    if (docked && roomId) setPreferences(current => current[roomId]?.modalOpen
      ? { ...current, [roomId]: { ...current[roomId], modalOpen: false } } : current);
  }, [docked, roomId]);
  return { workspaceRef, docked, panelWidth, open, setOpen };
}
