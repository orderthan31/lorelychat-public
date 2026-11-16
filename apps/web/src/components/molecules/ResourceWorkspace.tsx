import type { ReactNode } from 'react';

/** Scope responsive list columns to the available pane, not the viewport. */
export function ResourceWorkspace({ children }: { children: ReactNode }) {
  return <div className="resource-workspace" data-resource-workspace>
    <div className="resource-results">{children}</div>
  </div>;
}
