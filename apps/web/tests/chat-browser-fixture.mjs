// Optional real-browser fixture: import('/tests/chat-browser-fixture.mjs') then
// await runBrowserChatRegressions(). It only mounts synthetic data; no API writes.
import React, { Profiler, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { flushSync } from 'react-dom';
import { BubbleList } from '../src/components/organisms/BubbleList';
import { I18nProvider } from '../src/i18n/I18nProvider';

const pause = (ms = 100) => new Promise((resolve) => setTimeout(resolve, ms));
const assert = (condition, message) => { if (!condition) throw new Error(message); };
export async function runBrowserChatRegressions() {
  assert(document.visibilityState === 'visible', 'Run in a foreground tab: background tabs suspend animation frames');
  const host = document.createElement('div');
  host.id = 'chat-window-regression';
  host.style.cssText = 'position:fixed;inset:0;z-index:99999;background:white;display:flex;flex-direction:column';
  document.body.append(host);
  const messages = Array.from({ length: 5000 }, (_, index) => ({
    id: `fixture-${index}`, speaker_type: index % 2 ? 'character' : 'user', speaker_id: 'fixture-character',
    content: (`Fixture **${index}** text. `).repeat(1 + index % 13),
    ...(index % 10 === 0 ? { assets: [{ id: `image-${index}`, image_url: 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="400" height="200"%3E%3Crect width="400" height="200" fill="gray"/%3E%3C/svg%3E', label: 'fixture image' }] } : {}),
  }));
  const state = { items: messages, commits: [], characters: [], conversation: { id: 'fixture-room' } };
  function Fixture() {
    const [text, setText] = useState('');
    const [items, setItems] = useState(messages);
    const [renderImages, setRenderImages] = useState(true);
    state.setText = setText; state.setItems = setItems; state.setRenderImages = setRenderImages;
    const threadRef = useRef(null); const bottomRef = useRef(null);
    return React.createElement(I18nProvider, null,
      React.createElement('div', { style: { height: 'calc(100vh - 50px)' } },
        React.createElement(Profiler, { id: 'bubbles', onRender: (id, phase, actualDuration, baseDuration) => state.commits.push({ phase, actualDuration, baseDuration }) },
          React.createElement(BubbleList, { messages: items, characters: state.characters, conversation: state.conversation, threadRef, bottomRef, renderImages, onGenerateTts: () => {}, onScrollAwayChange: () => {} }))),
      React.createElement('input', { value: text, onChange: (event) => setText(event.target.value), 'aria-label': 'fixture composer' }));
  }
  const root = createRoot(host);
  const report = {};
  try {
    root.render(React.createElement(Fixture)); await pause(300);
    const node = host.querySelector('.chat-thread');
    const rows = () => [...host.querySelectorAll('[data-chat-row]')];
    report.initial = { retained: Number(host.querySelector('[data-chat-window]').dataset.totalMessages), mounted: rows().length, dom: host.querySelectorAll('*').length };
    assert(report.initial.retained === 5000 && report.initial.mounted <= 80, 'initial mounted budget');
    for (let step = 0; step < 4; step++) { node.scrollTop = node.scrollHeight; await pause(); }
    report.tail = { mounted: rows().length, first: rows()[0]?.dataset.chatRow, last: rows().at(-1)?.dataset.chatRow, scrollTop: node.scrollTop, scrollHeight: node.scrollHeight, clientHeight: node.clientHeight };
    assert(rows().some((row) => row.dataset.chatRow === 'fixture-4999'), `latest history must be reachable: ${JSON.stringify(report.tail)}`);
    node.scrollTop = node.scrollHeight / 2; await pause(300);
    const viewportTop = node.getBoundingClientRect().top;
    const anchor = rows().find((row) => row.getBoundingClientRect().bottom > viewportTop);
    const anchorId = anchor.dataset.chatRow;
    const anchorTop = anchor.getBoundingClientRect().top;
    const older = Array.from({ length: 100 }, (_, index) => ({ id: `older-${index}`, speaker_type: 'user', content: `Older ${index}` }));
    state.items = [...older, ...messages]; flushSync(() => state.setItems(state.items)); await pause(300);
    const preserved = rows().find((row) => row.dataset.chatRow === anchorId);
    assert(preserved, 'prepend must preserve the visible message');
    report.prependAnchorDelta = Math.abs(preserved.getBoundingClientRect().top - anchorTop);
    assert(report.prependAnchorDelta < 3, `prepend anchor moved ${report.prependAnchorDelta}px`);
    state.commits = [];
    for (let index = 0; index < 20; index++) flushSync(() => state.setText(`typed ${index}`));
    report.typing = { commits: state.commits.length, worstActualMs: Math.max(...state.commits.map((item) => item.actualDuration)), baseMs: Math.max(...state.commits.map((item) => item.baseDuration)) };
    assert(host.querySelector('input').value === 'typed 19', 'composer state updated');
    assert(report.typing.worstActualMs < report.typing.baseMs * 0.5 + 2, 'typing must skip the expensive list subtree');
    node.scrollTop = 0; await pause(300);
    assert(rows()[0]?.dataset.chatRow === 'older-0', 'oldest loaded history must remain reachable');
    report.oldest = { id: rows()[0].dataset.chatRow, mounted: rows().length, retained: Number(host.querySelector('[data-chat-window]').dataset.totalMessages) };
    assert(report.oldest.retained === 5100 && rows().length <= 80, 'prepend retained data without growing mounted DOM');
    flushSync(() => state.setItems(messages.slice(0, 553))); await pause(300);
    report.auditSized = { retained: Number(host.querySelector('[data-chat-window]').dataset.totalMessages), mounted: rows().length, dom: host.querySelectorAll('*').length };
    assert(report.auditSized.retained === 553 && report.auditSized.mounted <= 80, '553-message audit-sized fixture');
    host.style.width = '375px'; await pause(300);
    for (let step = 0; step < 4; step++) { node.scrollTop = node.scrollHeight; await pause(); }
    assert(rows().some((row) => row.dataset.chatRow === 'fixture-552'), 'mobile resize must retain tail navigation');
    flushSync(() => state.setRenderImages(false)); await pause(300);
    assert(rows().some((row) => row.dataset.chatRow === 'fixture-552'), 'image toggle must retain the latest view');
    assert(host.querySelectorAll('.bubble-assets img').length === 0, 'images disabled');
    report.mobileImagesOff = { mounted: rows().length, tail: rows().at(-1).dataset.chatRow, width: node.clientWidth };
    return report;
  } finally { root.unmount(); host.remove(); }
}
