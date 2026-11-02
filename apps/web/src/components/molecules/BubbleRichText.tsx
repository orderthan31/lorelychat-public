import { memo, useState } from 'react';
import { Button } from '../atoms/button';
import { useI18n } from '../../i18n/I18nProvider';
import type { Plugin } from 'unified';
import Markdown, { type Components } from 'react-markdown';

export const BUBBLE_MARKDOWN_ELEMENTS: string[] = ['p', 'strong', 'em', 'code', 'br'];
export type BubbleRichTextProps = { text: string; literalHtml?: boolean };
const MARKDOWN_COMPONENTS: Components = {
  p: ({ children }) => <>{children}</>,
  strong: ({ children }) => <strong className="font-semibold text-inherit">{children}</strong>,
  em: ({ children }) => <em className="italic text-inherit">{children}</em>,
  code: ({ children }) => <code className="rounded bg-current/10 px-1 py-0.5 font-mono text-[0.92em] text-inherit">{children}</code>,
  br: () => <br />,
};

export function BubbleRichText(props: BubbleRichTextProps) {
  return <MemoBubbleRichText {...props} />;
}

const MemoBubbleRichText = memo(function RichText({ text, literalHtml = false }: BubbleRichTextProps) {
  const [expanded, setExpanded] = useState(false);
  const { t } = useI18n();
  if (text.length > 24000 && !expanded) return <section data-large-prose><p className="text-xs">{t('큰 콘텐츠는 기본 텍스트로 표시됩니다.')}</p><pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words font-[inherit]">{text}</pre><Button type="button" className="min-h-11" onClick={() => setExpanded(true)}>{t('서식 펼치기')}</Button></section>;
  return <Markdown
    remarkPlugins={literalHtml ? [disableHtml] : []}
    allowedElements={BUBBLE_MARKDOWN_ELEMENTS}
    skipHtml
    unwrapDisallowed
    components={MARKDOWN_COMPONENTS}
  >{text}</Markdown>;
});

// Keep HTML-like source visible without enabling HTML nodes or execution.
const disableHtml: Plugin = function () {
  const data = this.data() as { micromarkExtensions?: { disable?: { null?: string[] } }[] };
  data.micromarkExtensions = [...(data.micromarkExtensions || []), { disable: { null: ['htmlFlow', 'htmlText'] } }];
}
