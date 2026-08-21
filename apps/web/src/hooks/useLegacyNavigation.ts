import { useCallback, useEffect, useState } from 'react';
import { appRouter, toRouterPath } from '../router/appRouter';
import { parseRoute, type LegacyRoute } from '../router/legacyRouter';
import { useUiStore } from '../stores/uiStore';

export type LegacyNavigationState = {
  route: LegacyRoute;
  navigate: (path: string) => void;
};

export function useLegacyNavigation(): LegacyNavigationState {
  const [route, setRoute] = useState<LegacyRoute>(parseRoute());
  const closeDrawers = useUiStore((state) => state.closeDrawers);

  const navigate = useCallback((path: string) => {
    const nextPath = toRouterPath(path);
    void appRouter.navigate({ to: nextPath }).catch(() => window.history.pushState({}, '', nextPath));
    setRoute(parseRoute(nextPath));
    closeDrawers();
  }, [closeDrawers]);

  useEffect(() => {
    const pop = () => setRoute(parseRoute());
    window.addEventListener('popstate', pop);
    return () => window.removeEventListener('popstate', pop);
  }, []);

  return { route, navigate };
}
