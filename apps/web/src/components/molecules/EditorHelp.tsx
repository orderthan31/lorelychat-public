import type { ReactNode } from 'react';
import './EditorHelp.scss';

/** Only optional explanatory UI copy belongs here; warnings and authored prompts stay visible. */
export function EditorHelp({ summary, children }: { summary: ReactNode; children: ReactNode }) {
  return <details className="editor-help"><summary>{summary}</summary><div>{children}</div></details>;
}
