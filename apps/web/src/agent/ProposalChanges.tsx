import { Component, lazy, Suspense, useMemo, useState, type ReactNode } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import type { UiMessageKey } from '../i18n/core';
import type { AgentDraft } from './types';

const A2uiProposalChanges = lazy(() => import('./A2uiProposalChanges'));
const fields: Partial<Record<string, UiMessageKey>> = {
  name: '이름', title: '제목', description: '설명', persona: '페르소나',
  location: '장소', mood: '분위기',
};

export type ProposalChange = { field: string; before: string; after: string };

export function ProposalChangeList({ changes, labels }: { changes: ProposalChange[]; labels: { before: string; after: string } }) {
  return <dl>
    {changes.map(({ field, before, after }) => <div key={field} className="agent-proposal-change">
      <dt>{field}</dt>
      {before && <dd><span>{labels.before}</span>{before}</dd>}
      <dd><span>{labels.after}</span>{after}</dd>
    </div>)}
  </dl>;
}

class ProposalPreviewBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

export function ProposalChanges({ draft }: { draft: AgentDraft }) {
  const { t } = useI18n();
  const changes = useMemo(() => {
    // Only known, plain-text fields from the existing reviewed draft. No remote
    // components, URLs, actions, or model-authored A2UI messages are accepted.
    const clip = (value: unknown) => typeof value === 'string' ? (value.length > 220 ? `${value.slice(0, 220)}…` : value) : '';
    return Object.entries(fields).flatMap(([key, label]) => {
      if (!label || !Object.hasOwn(draft.changes, key)) return [];
      const before = draft.before?.[key];
      const after = draft.after[key];
      if (before === after || (typeof after !== 'string' && after !== null)) return [];
      return [{ field: t(label), before: clip(before), after: clip(after) || '—' }];
    });
  }, [draft, t]);
  const labels = useMemo(() => ({ before: t('agent.proposalBefore'), after: t('agent.proposalAfter') }), [t]);
  const [opened, setOpened] = useState(false);
  if (!changes.length) return null;
  const fallback = <ProposalChangeList changes={changes} labels={labels} />;
  return <details className="agent-proposal-changes" onToggle={(event) => setOpened(event.currentTarget.open)}>
    <summary>{t('agent.proposalChanges')}</summary>
    {opened && <ProposalPreviewBoundary key={draft.preview_hash} fallback={fallback}>
      <Suspense fallback={fallback}><A2uiProposalChanges changes={changes} labels={labels} /></Suspense>
    </ProposalPreviewBoundary>}
  </details>;
}
