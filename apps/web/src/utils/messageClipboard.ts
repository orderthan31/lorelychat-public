import type { ChatMessage } from '../components/organisms/BubbleList';
import type { Translator } from '../i18n/core';

function messageTextPartsForCopy(message: ChatMessage): string[] {
  const parts = (Array.isArray(message.metadata?.render_parts) ? message.metadata.render_parts : [])
    .map(part => ({ type: String(part?.type || '').toLowerCase(), text: String(part?.text || '').trim() }))
    .filter(part => ['dialogue', 'action', 'thought'].includes(part.type) && part.text);
  if (parts.length) {
    const freeBody = ['tagged_text', 'free_body_json'].includes(message.metadata?.response_protocol || '');
    return [
      ...parts.map(part => part.type === 'thought' ? `(${part.text})` : part.text),
      ...(!freeBody && !parts.some(part => part.type === 'thought') && message.thought ? [`(${message.thought})`] : []),
    ];
  }
  return [message.action, message.content, message.thought && `(${message.thought})`].filter(Boolean) as string[];
}

export function messageTextForCopy(message: ChatMessage, t: Translator): string {
  const blocks = Array.isArray(message.metadata?.command_blocks) ? message.metadata.command_blocks : [];
  const blockText = blocks.map(block => {
    if (block.type === 'table') return [block.title, ...(block.rows || []).map(row => row.join(' | '))].filter(Boolean).join('\n');
    if (block.type === 'comments' || block.type === 'commentary') return [block.title || t('LIVE 댓글'), ...(block.comments || []).map(item => `${item.author || t('시청자')}: ${item.text || ''}`)].join('\n');
    return [block.title, block.text].filter(Boolean).join('\n');
  }).filter(Boolean).join('\n\n');
  return [...messageTextPartsForCopy(message), blockText].filter(Boolean).join('\n');
}

// Private-mesh HTTP origins may not expose the secure-context Clipboard API.
// The fallback still requires a real user gesture and a successful copy result.
export async function writeMessageTextToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const focused = document.activeElement;
  const selection = document.getSelection();
  const ranges = selection ? Array.from({ length: selection.rangeCount }, (_, index) => selection.getRangeAt(index).cloneRange()) : [];
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.readOnly = true;
  textarea.dataset.clipboardFallback = '';
  textarea.style.cssText = 'position:fixed;left:-9999px;top:0;opacity:0;';
  document.body.appendChild(textarea);
  try {
    textarea.select();
    if (!document.execCommand('copy')) throw new Error('Clipboard copy unavailable');
  } finally {
    textarea.remove();
    if (focused instanceof HTMLElement) focused.focus({ preventScroll: true });
    if (selection && ranges.length) {
      selection.removeAllRanges();
      ranges.forEach(range => selection.addRange(range));
    }
  }
}
