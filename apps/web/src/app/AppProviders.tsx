import type { ReactNode } from 'react';
import { AgentProvider } from '../agent/components';
import { QueryClientProvider } from '@tanstack/react-query';
import { I18nProvider } from '../i18n/I18nProvider';
import { queryClient } from './queryClient';
import { LorechatTheme } from './LorechatTheme';

export type AppProvidersProps = {
  children: ReactNode;
};

export function AppProviders({ children }: AppProvidersProps) {
  return <I18nProvider><QueryClientProvider client={queryClient}><LorechatTheme><AgentProvider>{children}</AgentProvider></LorechatTheme></QueryClientProvider></I18nProvider>;
}
