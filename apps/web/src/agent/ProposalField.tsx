import type { ReactNode } from 'react';

export const proposalHighlight = { backgroundColor: 'rgba(99, 102, 241, 0.08)', boxShadow: 'inset 3px 0 0 rgb(99, 102, 241)', borderRadius: '0.375rem', padding: '0.5rem' };
export type ProposalFormProps = { reviewMode?: boolean; changedFields?: readonly string[]; onCancelReview?: () => void };
export function ProposalField({ name, changedFields, children }: { name: string; changedFields?: readonly string[]; children: ReactNode }) {
  const changed = changedFields?.includes(name);
  return <div data-proposal-field={changed ? name : undefined} style={changed ? proposalHighlight : { display: 'contents' }}>{children}</div>;
}
