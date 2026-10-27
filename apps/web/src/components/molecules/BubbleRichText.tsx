import { memo } from 'react';
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
