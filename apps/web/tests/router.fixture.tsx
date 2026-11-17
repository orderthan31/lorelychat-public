import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from '@tanstack/react-router';
import { createAppRouter } from '../src/router/appRouter';
import { useLegacyNavigation } from '../src/hooks/useLegacyNavigation';
import { useUiStore } from '../src/stores/uiStore';
function Fixture() {
  const { route, navigate } = useLegacyNavigation();
  const [draft, setDraft] = useState('');
  const drawerOpen = useUiStore(s => s.drawerOpen);
  return <main>
    <output data-route>{JSON.stringify(route)}</output>
    <input aria-label="persistent draft" value={draft} onChange={e => setDraft(e.target.value)} />
    <input aria-label="destination" id="destination" />
    <button onClick={() => navigate((document.getElementById('destination') as HTMLInputElement).value)}>Navigate</button>
    <button onClick={() => useUiStore.setState({ drawerOpen: true })}>Open drawer</button>
    <output data-drawer>{String(drawerOpen)}</output>
  </main>;
}
const router = createAppRouter(Fixture);
createRoot(document.getElementById('root')!).render(<RouterProvider router={router} />);
