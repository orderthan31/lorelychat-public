import { memo } from 'react';
import Markdown, { type Components } from 'react-markdown';

// No raw HTML plugin, embeds, or image requests from model-authored content.
const components: Components = {
  a: ({ href, children }) => href && /^(https?:\/\/|mailto:)/i.test(href)
    ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
    : <span>{children}</span>,
  img: ({ alt }) => <span>{alt}</span>,
};
export const AgentMarkdown = memo(function AgentMarkdown({ text }: { text: string }) {
  return <div className="agent-markdown"><Markdown skipHtml components={components}>{text}</Markdown></div>;
});
