export type RenderPart = {
  type: 'dialogue' | 'action' | 'thought';
  text: string;
};

export type ParsedInputMarkup = {
  dialogue: string;
  action: string;
  hadMarkup: boolean;
  parts: RenderPart[];
};

export function linesToArray(value: unknown): string[] {
  return String(value ?? '').replace(/\r\n/g, '\n').split('\n');
}

export function normalizeSegmentText(value: unknown): string {
  return String(value || '').split(/\s+/).filter(Boolean).join(' ');
}

function isSingleAsteriskMarker(text: string, index: number): boolean {
  return text[index] === '*' && text[index - 1] !== '*' && text[index + 1] !== '*';
}

function findNextActionMarker(text: string, cursor: number): { index: number; marker: '*' } | null {
  let best: { index: number; marker: '*' } | null = null;
  for (let index = cursor; index < text.length; index += 1) {
    const marker = isSingleAsteriskMarker(text, index) ? '*' : null;
    if (marker) {
      best = { index, marker };
      break;
    }
  }
  return best;
}

function findClosingActionMarker(text: string, cursor: number, marker: '*'): number {
  for (let index = cursor; index < text.length; index += 1) {
    if (isSingleAsteriskMarker(text, index)) return index;
  }
  return -1;
}

export function parseInputMarkup(raw: unknown): ParsedInputMarkup {
  const text = String(raw || '');
  const dialogueParts: string[] = [];
  const actionParts: string[] = [];
  const parts: RenderPart[] = [];
  let cursor = 0;
  let hadMarkup = false;
  while (cursor < text.length) {
    const start = findNextActionMarker(text, cursor);
    if (!start) {
      const dialogue = normalizeSegmentText(text.slice(cursor));
      if (dialogue) { dialogueParts.push(dialogue); parts.push({ type: 'dialogue', text: dialogue }); }
      break;
    }
    const markerLength = start.marker.length;
    const end = findClosingActionMarker(text, start.index + markerLength, start.marker);
    if (end < 0) {
      const dialogue = normalizeSegmentText(text.slice(cursor));
      if (dialogue) { dialogueParts.push(dialogue); parts.push({ type: 'dialogue', text: dialogue }); }
      break;
    }
    const dialogue = normalizeSegmentText(text.slice(cursor, start.index));
    if (dialogue) { dialogueParts.push(dialogue); parts.push({ type: 'dialogue', text: dialogue }); }
    const action = normalizeSegmentText(text.slice(start.index + markerLength, end));
    if (action) { actionParts.push(action); parts.push({ type: 'action', text: action }); hadMarkup = true; }
    cursor = end + markerLength;
  }
  return { dialogue: normalizeSegmentText(dialogueParts.join(' ')), action: normalizeSegmentText(actionParts.join(' ')), hadMarkup, parts };
}

export function cleanRuleLines(value: unknown): string[] {
  const lines = Array.isArray(value) ? value : linesToArray(value);
  return lines.map((line) => String(line).replace(/\r/g, '')).filter((line) => line.trim().length > 0);
}

export function arrayToLines(value: unknown): string {
  return Array.isArray(value) ? value.join('\n') : '';
}

export function compactText(value: unknown, fallback = '-'): string {
  return value === null || value === undefined || value === '' ? fallback : String(value);
}

export function tagText(value: unknown): string {
  return Array.isArray(value) ? value.join(', ') : String(value || '');
}

export function parseTagInput(value: unknown): string[] {
  return String(value || '').split(',').map((item) => item.trim()).filter(Boolean);
}

export function safeJsonParse(value: unknown): [unknown, null] | [null, unknown] {
  try { return [JSON.parse(String(value || '')), null]; }
  catch (error) { return [null, error]; }
}
