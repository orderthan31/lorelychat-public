import Markdown from 'react-markdown';

export const BUBBLE_MARKDOWN_ELEMENTS: string[] = ['p', 'strong', 'em', 'code', 'br'];

export type BubbleRichTextProps = {
  text: string;
};

export function BubbleRichText({ text }: BubbleRichTextProps) {
  return <Markdown
    allowedElements={BUBBLE_MARKDOWN_ELEMENTS}
    skipHtml
    unwrapDisallowed
    components={{
      p: ({ children }) => <>{children}</>,
      strong: ({ children }) => <strong className="font-semibold text-inherit">{children}</strong>,
      em: ({ children }) => <em className="italic text-inherit">{children}</em>,
      code: ({ children }) => <code className="rounded bg-current/10 px-1 py-0.5 font-mono text-[0.92em] text-inherit">{children}</code>,
      br: () => <br />,
    }}
  >{text}</Markdown>;
}
