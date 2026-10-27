import { useLayoutEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { chatWindow, messageOffsets, rowAtOffset, type WindowMessage } from '../utils/chatWindow';

// Keep the complete data history; only Markdown/media DOM is windowed.
// Measured heights preserve native scrollbar navigation, including old pages.
export function useChatWindow(messages: WindowMessage[], threadRef: RefObject<HTMLDivElement>) {
  const listRef = useRef<HTMLDivElement>(null);
  const heights = useRef(new Map<string, number>());
  const [measurement, setMeasurement] = useState(0);
  const [viewport, setViewport] = useState({ top: 0, height: 800 });
  const offsets = useMemo(() => messageOffsets(messages, heights.current), [messages, measurement]);
  const range = chatWindow(offsets, viewport.top, viewport.height);
  const previous = useRef({ messages, offsets });


  const readViewport = () => {
    const node = threadRef.current;
    const list = listRef.current;
    if (!node || !list) return;
    const top = node.getBoundingClientRect().top - list.getBoundingClientRect().top;
    const height = node.clientHeight;
    setViewport((old) => old.top === top && old.height === height ? old : { top, height });
  };

  useLayoutEffect(() => {
    const node = threadRef.current;
    const list = listRef.current;
    if (!node || !list) return;
    // Preserve a message ID + intra-row offset across both prepend and height
    // refinements. Native overflow anchoring is disabled to avoid double deltas.
    const old = previous.current;
    if (old.offsets !== offsets && old.messages.length) {
      const index = rowAtOffset(old.offsets, viewport.top);
      const anchorId = old.messages[index]?.id;
      const nextIndex = old.messages === messages ? index : messages.findIndex((message) => message.id === anchorId);
      if (nextIndex >= 0) {
        const delta = offsets[nextIndex] - old.offsets[index];
        if (delta) node.scrollTop += delta;
      }
      // Measured media growth at the tail should retain the latest view.
      if (old.messages === messages && viewport.top + viewport.height >= old.offsets[old.offsets.length - 1] - 24) node.scrollTop = node.scrollHeight;
    }
    previous.current = { messages, offsets };
    readViewport();
  }, [messages, offsets]);

  useLayoutEffect(() => {
    const node = threadRef.current;
    if (!node) return;
    let frame = 0;
    const update = () => {
      if (!frame) frame = requestAnimationFrame(() => { frame = 0; readViewport(); });
    };
    node.addEventListener('scroll', update, { passive: true });
    let width = node.clientWidth;
    const resize = new ResizeObserver(() => {
      if (width !== node.clientWidth) {
        width = node.clientWidth;
        heights.current.clear();
        setMeasurement((value) => value + 1);
      }
      update();
    });
    resize.observe(node);
    return () => { node.removeEventListener('scroll', update); resize.disconnect(); cancelAnimationFrame(frame); };
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
