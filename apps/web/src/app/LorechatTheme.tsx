import type { ReactNode } from 'react';
import { defineTheme, Theme } from '@astryxdesign/core';
import { neutralTheme } from '@astryxdesign/theme-neutral/built';
import { useUiStore } from '../stores/uiStore';

const lorechatTheme = defineTheme({
  name: 'lorechat',
  extends: neutralTheme,
  tokens: {
    '--color-accent': 'color-mix(in srgb, var(--primary) 75%, #000)',
    '--color-accent-muted': 'var(--primary-soft)',
    '--color-on-accent': 'var(--primary-foreground)',
    '--color-text-accent': 'var(--primary)',
    '--color-icon-accent': 'var(--primary)',
    '--color-neutral': 'var(--surface-muted)',
    '--color-background-body': 'var(--bg)',
    '--color-background-surface': 'var(--panel)',
    '--color-background-muted': 'var(--surface-muted)',
    '--color-background-card': 'var(--card)',
    '--color-background-popover': 'var(--card)',
    '--color-text-primary': 'var(--foreground)',
    '--color-text-secondary': 'var(--muted)',
    '--color-icon-primary': 'var(--foreground)',
    '--color-icon-secondary': 'var(--muted)',
    '--color-border': 'var(--border)',
    '--color-border-emphasized': 'var(--line)',
    '--color-info': 'var(--info)',
    '--color-success': 'var(--success)',
    '--color-warning': 'var(--warning)',
    '--color-error': 'color-mix(in srgb, var(--danger) 82%, #000)',
    '--color-on-error': 'var(--primary-foreground)',
  },
});

export function LorechatTheme({ children }: { children: ReactNode }) {
  const theme = useUiStore(state => state.theme);
  return <Theme theme={lorechatTheme} mode={theme}>{children}</Theme>;
}
