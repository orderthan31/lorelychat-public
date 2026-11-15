import { useLayoutEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { chatWindow, messageOffsets, rowAtOffset, type WindowMessage } from '../utils/chatWindow';

const SCROLL_INTENT_EVENT = 'chat-scroll-intent';

// Tell the measured window the intent, not just a one-frame pixel position.
// Its tail can grow after Markdown, fonts and images finish laying out.
export function setChatScrollIntent(node: HTMLElement, intent: 'latest' | 'preserve') {
  node.dispatchEvent(new CustomEvent(SCROLL_INTENT_EVENT, { detail: intent }));
  if (intent === 'latest') node.scrollTop = node.scrollHeight;
}

// Keep the complete data history; only Markdown/media DOM is windowed.
export function useChatWindow(messages: WindowMessage[], threadRef: RefObject<HTMLDivElement>) {
  const listRef = useRef<HTMLDivElement>(null);
  const heights = useRef(new Map<string, number>());
  const [measurement, setMeasurement] = useState(0);
  const [viewport, setViewport] = useState({ top: 0, height: 800 });
  const viewportRef = useRef(viewport);
  const followingLatest = useRef(true);
  const lastScrollTop = useRef(0);
  const offsets = useMemo(() => messageOffsets(messages, heights.current), [messages, measurement]);
  const range = chatWindow(offsets, viewport.top, viewport.height);
  const previous = useRef({ messages, offsets });
  const mounted = useRef(false);

  const readViewport = () => {
    const node = threadRef.current;
    const list = listRef.current;
    if (!node || !list) return;
    const top = node.getBoundingClientRect().top - list.getBoundingClientRect().top;
    const height = node.clientHeight;
    lastScrollTop.current = node.scrollTop;
    viewportRef.current = { top, height };
    setViewport((old) => old.top === top && old.height === height ? old : { top, height });
  };

  useLayoutEffect(() => {
    const node = threadRef.current;
    const list = listRef.current;
    if (!node || !list) return;
    const old = previous.current;
    // Initial entry is an intent that survives estimated -> measured rows.
    // Snapshot replacement/prepend instead retains an ID + intra-row offset;
    // the caller may explicitly request latest after committing an append.
    if (followingLatest.current && (!mounted.current || !old.messages.length || old.messages === messages)) {
      node.scrollTop = node.scrollHeight;
    } else if (old.offsets !== offsets && old.messages.length) {
      const index = rowAtOffset(old.offsets, viewportRef.current.top);
      const anchorId = old.messages[index]?.id;
      const nextIndex = old.messages === messages ? index : messages.findIndex((message) => message.id === anchorId);
      if (nextIndex >= 0) {
        const delta = offsets[nextIndex] - old.offsets[index];
        if (delta) node.scrollTop += delta;
      }
    }
    mounted.current = true;
    previous.current = { messages, offsets };
    readViewport();
  }, [messages, offsets]);

  useLayoutEffect(() => {
    const node = threadRef.current;
    if (!node) return;
    const update = () => {
      // Only an actual move upward relinquishes tail following. Measurement
      // growth is not a user scroll, and our own writes update this ref first.
      if (node.scrollTop < lastScrollTop.current - 1) followingLatest.current = false;
      else if (node.scrollHeight - node.scrollTop - node.clientHeight <= 24) followingLatest.current = true;
      readViewport();
    };
    const intent = (event: Event) => {
      followingLatest.current = (event as CustomEvent).detail === 'latest';
      if (followingLatest.current) node.scrollTop = node.scrollHeight;
      readViewport();
    };
    const wheel = (event: WheelEvent) => { if (event.deltaY < 0) followingLatest.current = false; };
    const touch = () => { followingLatest.current = false; };
    const key = (event: KeyboardEvent) => {
      if (['ArrowUp', 'PageUp', 'Home'].includes(event.key) || (event.key === ' ' && event.shiftKey)) followingLatest.current = false;
    };
    node.addEventListener('scroll', update, { passive: true });
    node.addEventListener(SCROLL_INTENT_EVENT, intent);
    node.addEventListener('wheel', wheel, { passive: true });
    node.addEventListener('touchmove', touch, { passive: true });
    node.addEventListener('keydown', key);
    let width = node.clientWidth;
    const resize = new ResizeObserver(() => {
      if (width !== node.clientWidth) {
        width = node.clientWidth;
        heights.current.clear();
        setMeasurement((value) => value + 1);
      }
      if (followingLatest.current) node.scrollTop = node.scrollHeight;
      readViewport();
    });
    resize.observe(node);
    return () => {
      node.removeEventListener('scroll', update);
      node.removeEventListener(SCROLL_INTENT_EVENT, intent);
      node.removeEventListener('wheel', wheel);
      node.removeEventListener('touchmove', touch);
      node.removeEventListener('keydown', key);
      resize.disconnect();
    };
  }, [threadRef]);

  useLayoutEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const measure = () => {
      let changed = false;
      for (const element of list.querySelectorAll<HTMLElement>('[data-chat-row]')) {
        const id = element.dataset.chatRow!;
        const height = element.getBoundingClientRect().height;
        if (height > 0 && Math.abs((heights.current.get(id) ?? -1) - height) > 0.5) {
          heights.current.set(id, height);
          changed = true;
        }
      }
      if (changed) setMeasurement((value) => value + 1);
    };
    measure();
    const resize = new ResizeObserver(measure);
    for (const row of list.querySelectorAll('[data-chat-row]')) resize.observe(row);
    return () => resize.disconnect();
  }, [messages, range.start, range.end, measurement]);

  useLayoutEffect(() => {
    const ids = new Set(messages.map((message, index) => message.id || `row_${index}`));
    for (const id of heights.current.keys()) if (!ids.has(id)) heights.current.delete(id);
  }, [messages]);

  return { listRef, start: range.start, end: range.end, top: offsets[range.start], total: offsets[offsets.length - 1] };
}
