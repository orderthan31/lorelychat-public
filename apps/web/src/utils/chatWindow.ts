export const CHAT_MOUNT_BUDGET = 80;
export const CHAT_ROW_ESTIMATE = 160;
export const CHAT_ROW_GAP = 7;
export type WindowMessage = { id?: string };

export function messageOffsets(messages: WindowMessage[], heights: Map<string, number>) {
  const offsets = [0];
  for (let index = 0; index < messages.length; index += 1) {
    offsets.push(offsets[index] + (heights.get(messages[index].id || `row_${index}`) ?? CHAT_ROW_ESTIMATE) + CHAT_ROW_GAP);
  }
  return offsets;
}

export function rowAtOffset(offsets: number[], position: number) {
  let low = 0;
  let high = Math.max(0, offsets.length - 2);
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (offsets[middle] <= position) low = middle;
    else high = middle - 1;
  }
  return low;
}

export function chatWindow(offsets: number[], scrollTop: number, viewportHeight: number) {
  const count = offsets.length - 1;
  if (!count) return { start: 0, end: 0 };
  const first = rowAtOffset(offsets, Math.max(0, scrollTop));
  const last = rowAtOffset(offsets, Math.max(0, scrollTop) + viewportHeight);
  const start = Math.max(0, first - 8);
  const end = Math.min(count, Math.max(start + 24, last + 9), start + CHAT_MOUNT_BUDGET);
  return { start, end };
}
