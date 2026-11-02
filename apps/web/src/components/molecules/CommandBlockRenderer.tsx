import { useState } from 'react';
import type * as React from 'react';
import { Button } from '../atoms/button';
import { cn } from '../../lib/utils';
import { useI18n } from '../../i18n/I18nProvider';

export type CommandComment = { author?: string; text?: string };
export type CommandChecklistItem = { text?: string; checked?: boolean };
export type CommandTimelineItem = { time?: string; text?: string };
export type CommandVoteOption = { label?: string; value?: number | string | null };
export type CommandCard = { title?: string; text?: string };
export type CommandBlock = {
  type?: string;
  title?: string;
  text?: string;
  headers?: string[];
  rows?: string[][];
  comments?: CommandComment[];
  items?: CommandChecklistItem[] | CommandTimelineItem[];
  options?: CommandVoteOption[];
  cards?: CommandCard[];
};

function CommandBlockShell({ title, tone = 'default', children }: { title?: string; tone?: 'default' | 'note'; children: React.ReactNode }) {
  return <section className={cn(
    'grid gap-1.5 rounded-xl border p-2 text-xs leading-relaxed text-card-foreground shadow-sm shadow-foreground/5',
    tone === 'note' ? 'border-warning bg-card' : 'border-border bg-card',
  )}>
    {title && <div className={cn('mb-0 text-[11px] font-black tracking-[.02em]', tone === 'note' ? 'text-warning' : 'text-primary')}>{title}</div>}
    {children}
  </section>;
}

export function validCommandBlock(block: unknown): block is CommandBlock {
  if (!block || typeof block !== 'object' || Array.isArray(block)) return false;
  const b = block as Record<string, unknown>;
  for (const key of ['type', 'title', 'text']) if (b[key] !== undefined && typeof b[key] !== 'string') return false;
  if (b.headers !== undefined && (!Array.isArray(b.headers) || b.headers.some(v => typeof v !== 'string'))) return false;
  if (b.rows !== undefined && (!Array.isArray(b.rows) || b.rows.some(row => !Array.isArray(row) || row.some(v => typeof v !== 'string')))) return false;
  for (const key of ['comments', 'items', 'options', 'cards']) {
    if (b[key] === undefined) continue;
    if (!Array.isArray(b[key]) || (b[key] as unknown[]).some(item => !item || typeof item !== 'object' || Array.isArray(item) || Object.entries(item).some(([name, value]) => ['author', 'text', 'time', 'label', 'title'].includes(name) ? typeof value !== 'string' : name === 'checked' ? typeof value !== 'boolean' : name === 'value' ? value !== null && !['string', 'number'].includes(typeof value) : false))) return false;
  }
  return true;
}

export function CommandBlockRenderer({ block, index }: { block: CommandBlock; index: number }) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  if (!validCommandBlock(block)) return <section role="status" data-malformed-command><p>{t('표시할 수 없는 콘텐츠')}</p><details><summary>{t('원본 보기')}</summary><pre className="whitespace-pre-wrap break-all">{JSON.stringify(block, null, 2)}</pre></details></section>;
  const cells = (block.rows || []).reduce((n, row) => n + row.length, 0);
  const entries = ['comments', 'items', 'options', 'cards'].reduce((n, key) => n + ((block as any)[key]?.length || 0), cells);
  if (entries > 400 && !expanded) return <section data-large-command><p>{t('큰 콘텐츠는 기본 텍스트로 표시됩니다.')}</p><pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(block, null, 2)}</pre><Button type="button" className="min-h-11" onClick={() => setExpanded(true)}>{t('서식 펼치기')}</Button></section>;
  const type = String(block.type || 'text').toLowerCase();
  if (type === 'comments' || type === 'commentary') {
    const comments = Array.isArray(block.comments) ? block.comments.filter((item) => item?.text) : [];
    if (!comments.length) return null;
    return <CommandBlockShell key={index} title={block.title || t('댓글/반응')}>
      <div className="grid gap-1.5">{comments.map((comment, commentIndex) => <div className="grid grid-cols-[minmax(54px,auto)_minmax(0,1fr)] items-start gap-2" key={`${index}-${commentIndex}`}><strong className="truncate font-black text-accent">{comment.author || t('익명')}</strong><span className="whitespace-pre-wrap text-card-foreground/85">{comment.text}</span></div>)}</div>
    </CommandBlockShell>;
  }
  if (type === 'table') {
    const rows = Array.isArray(block.rows) ? block.rows : [];
    if (!rows.length) return null;
    const headers = Array.isArray(block.headers) ? block.headers : [];
    return <CommandBlockShell key={index} title={block.title}>
      <div className="max-w-full overflow-x-auto"><table className="w-full border-collapse text-[11px] text-card-foreground/85">
        {headers.length > 0 && <thead><tr>{headers.map((header, headerIndex) => <th className="border border-border bg-card px-1.5 py-1 text-left align-top font-black text-accent" key={headerIndex}>{header}</th>)}</tr></thead>}
        <tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td className="border border-border px-1.5 py-1 text-left align-top" key={cellIndex}>{cell}</td>)}</tr>)}</tbody>
      </table></div>
    </CommandBlockShell>;
  }
  if (type === 'checklist') {
    const items = Array.isArray(block.items) ? block.items.filter((item) => item?.text) as CommandChecklistItem[] : [];
    if (!items.length) return null;
    return <CommandBlockShell key={index} title={block.title}><ul className="grid list-none gap-1.5 p-0 text-card-foreground/85">{items.map((item, itemIndex) => <li key={`${index}-${itemIndex}`} className={cn('grid grid-cols-[auto_minmax(0,1fr)] items-start gap-2', item.checked && 'text-muted-foreground line-through')}><span className="font-black text-success">{item.checked ? '✓' : '○'}</span><span>{item.text}</span></li>)}</ul></CommandBlockShell>;
  }
  if (type === 'timeline') {
    const items = Array.isArray(block.items) ? block.items.filter((item) => item?.text) as CommandTimelineItem[] : [];
    if (!items.length) return null;
    return <CommandBlockShell key={index} title={block.title}><ol className="grid list-none gap-1.5 p-0 text-card-foreground/85">{items.map((item, itemIndex) => <li key={`${index}-${itemIndex}`} className="grid grid-cols-[auto_minmax(0,1fr)] items-start gap-2"><strong className="font-black text-success">{item.time || '•'}</strong><span>{item.text}</span></li>)}</ol></CommandBlockShell>;
  }
  if (type === 'vote') {
    const options = Array.isArray(block.options) ? block.options.filter((item) => item?.label) : [];
    if (!options.length) return null;
    const numericValues = options.map((item) => Number(item.value)).filter((value) => Number.isFinite(value) && value > 0);
    const max = numericValues.reduce((max, value) => Math.max(max, value), 100);
    return <CommandBlockShell key={index} title={block.title}><div className="grid gap-2">{options.map((option, optionIndex) => { const value = Number(option.value); const width = Number.isFinite(value) && value > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0; return <div key={`${index}-${optionIndex}`}><div className="flex justify-between gap-2 text-card-foreground/90"><span>{option.label}</span>{option.value !== undefined && option.value !== null && <strong className="font-black text-accent">{String(option.value)}</strong>}</div><div className="mt-1 h-1.5 overflow-hidden rounded-full bg-[linear-gradient(90deg,#ffe8ec,#fff4f6)] dark:bg-[linear-gradient(90deg,rgba(98,111,134,.34),rgba(98,111,134,.34))]"><span className="block h-full rounded-full bg-[linear-gradient(90deg,#ff9aaa_0%,#f05a68_48%,#b91c3a_100%)] dark:bg-[linear-gradient(90deg,color-mix(in_srgb,var(--success)_88%,#fff),color-mix(in_srgb,var(--accent-2)_76%,#fff))]" style={{ width: `${width}%` }} /></div></div>; })}</div></CommandBlockShell>;
  }
  if (type === 'cards') {
    const cards = Array.isArray(block.cards) ? block.cards.filter((item) => item?.title || item?.text) : [];
    if (!cards.length) return null;
    return <CommandBlockShell key={index} title={block.title}><div className="grid gap-1.5">{cards.map((card, cardIndex) => <div className="grid gap-1 rounded-lg border border-border bg-background p-2 text-xs text-card-foreground shadow-inner shadow-foreground/5" key={`${index}-${cardIndex}`}>{card.title && <strong className="font-black text-accent">{card.title}</strong>}{card.text && <span className="text-card-foreground/85">{card.text}</span>}</div>)}</div></CommandBlockShell>;
  }
  const text = block.text || '';
  if (!text) return null;
  return <CommandBlockShell key={index} title={block.title} tone={type === 'note' ? 'note' : 'default'}>
    <div className="whitespace-pre-wrap text-card-foreground/85">{text}</div>
  </CommandBlockShell>;
}
