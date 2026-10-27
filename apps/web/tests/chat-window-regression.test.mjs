import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

const source = readFileSync(new URL('../src/utils/chatWindow.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { messageOffsets, chatWindow, rowAtOffset, CHAT_MOUNT_BUDGET } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const messages = Array.from({ length: 5000 }, (_, index) => ({ id: `message-${index}`, speaker_type: 'user', content: `Message **${index}** with *Markdown*`, metadata: { render_parts: [{ type: 'dialogue', text: `Message **${index}**` }] } }));
const heights = new Map(messages.map((message, index) => [message.id, 35 + (index % 17) * 29]));
const offsets = messageOffsets(messages, heights);
const visited = new Set();
let maximum = 0;
for (let top = 0; top < offsets.at(-1); top += 450) {
  const { start, end } = chatWindow(offsets, top, 900);
  maximum = Math.max(maximum, end - start);
  assert.ok(end - start <= CHAT_MOUNT_BUDGET);
  assert.ok(start <= rowAtOffset(offsets, top));
  assert.ok(end > rowAtOffset(offsets, Math.min(offsets.at(-1) - 1, top + 900)));
  for (const message of messages.slice(start, end)) visited.add(message.id);
}
assert.equal(visited.size, messages.length, 'every historical message is reachable by ordinary scrolling');
assert.equal(chatWindow(offsets, 0, 900).start, 0);
assert.equal(chatWindow(offsets, offsets.at(-1) - 900, 900).end, messages.length);
assert.deepEqual(chatWindow([0], 0, 900), { start: 0, end: 0 });
assert.ok(chatWindow(messageOffsets(messages, new Map()), 10000, 100000).end - chatWindow(messageOffsets(messages, new Map()), 10000, 100000).start <= CHAT_MOUNT_BUDGET);
// A prepended page and measured height corrections preserve the old anchor's
// intra-row offset, rather than discarding history to enforce the DOM budget.
const anchor = 2500; const within = 28;
const older = Array.from({ length: 100 }, (_, index) => ({ id: `older-${index}` }));
const prepended = [...older, ...messages];
const newOffsets = messageOffsets(prepended, heights);
const anchoredTop = offsets[anchor] + within + newOffsets[anchor + older.length] - offsets[anchor];
assert.equal(rowAtOffset(newOffsets, anchoredTop), anchor + older.length);
assert.equal(anchoredTop - newOffsets[anchor + older.length], within);
console.log(`window navigation: all ${visited.size} messages reachable; max ${maximum}/${CHAT_MOUNT_BUDGET} rows`);

// Render the actual production component and real Markdown stack. A data-only
// selector test would not catch accidentally mapping the full array in JSX.
const server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom', logLevel: 'error' });
try {
  const { BubbleList } = await server.ssrLoadModule('/src/components/organisms/BubbleList.tsx');
  const { I18nProvider } = await server.ssrLoadModule('/src/i18n/I18nProvider.tsx');
  const render = (count) => renderToStaticMarkup(React.createElement(I18nProvider, null, React.createElement(BubbleList, {
    messages: messages.slice(0, count), characters: [], bottomRef: { current: null }, threadRef: { current: null }, conversation: { id: 'fixture' },
  })));
  for (const count of [25, 100, 553, 5000]) {
    const html = render(count);
    const articles = (html.match(/<article\b/g) || []).length;
    assert.ok(articles <= CHAT_MOUNT_BUDGET, `${count} messages mounted ${articles} articles`);
    assert.match(html, new RegExp(`data-total-messages="${count}"`));
    assert.match(html, /role="listitem"/);
    assert.match(html, /<strong/);
    console.log(`production BubbleList SSR: ${count} retained messages → ${articles} mounted bubbles`);
  }
} finally {
  await server.close();
}
console.log('chat window behavior and real component render regressions passed');
