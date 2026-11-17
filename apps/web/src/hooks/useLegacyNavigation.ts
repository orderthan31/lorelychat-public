import { useCallback } from 'react';
import { useRouter, useRouterState } from '@tanstack/react-router';
import { toRouterPath } from '../router/appRouter';
import { parseRoute, type LegacyRoute } from '../router/legacyRouter';
import { useUiStore } from '../stores/uiStore';

export type LegacyNavigationState = {
  route: LegacyRoute;
  navigate: (path: string) => void;
};

export function useLegacyNavigation(): LegacyNavigationState {
  const router = useRouter();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const closeDrawers = useUiStore((state) => state.closeDrawers);
  const navigate = useCallback((path: string) => {
    void router.navigate({ to: toRouterPath(path) });
    closeDrawers();
  }, [router, closeDrawers]);
  return { route: parseRoute(pathname), navigate };
}
