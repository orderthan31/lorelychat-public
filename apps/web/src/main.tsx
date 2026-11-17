import { RouterProvider } from '@tanstack/react-router';
import { createAppRouter } from './router/appRouter';
import { createRoot } from 'react-dom/client';
import { App } from './app/App';
import { AppErrorBoundary } from './app/AppErrorBoundary';
import { AppProviders } from './app/AppProviders';
import './styles.css';

const root = document.getElementById('root');
if (!root) throw new Error('Root element not found');

const router = createAppRouter(App);
createRoot(root).render(<AppErrorBoundary><AppProviders><RouterProvider router={router} /></AppProviders></AppErrorBoundary>);
