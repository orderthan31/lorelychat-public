import { createContext, useContext, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

export const PageHeaderTarget = createContext<HTMLElement | null>(null);
/** Explicitly carries review protection across the DOM portal boundary. */
export const PageActionDisabled = createContext(false);
export function PageHeaderAction({ children }: { children: ReactNode }) {
  const target = useContext(PageHeaderTarget);
  const disabled = useContext(PageActionDisabled);
  const content = <fieldset className="page-header-primary" disabled={disabled}>{children}</fieldset>;
  return target ? createPortal(content, target) : content;
}
