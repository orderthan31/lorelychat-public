import { createRoot } from 'react-dom/client';
import { App } from './app/App';
import { AppErrorBoundary } from './app/AppErrorBoundary';
import { AppProviders } from './app/AppProviders';
import './styles.css';

const root = document.getElementById('root');
if (!root) throw new Error('Root element not found');

createRoot(root).render(<AppErrorBoundary><AppProviders><App /></AppProviders></AppErrorBoundary>);
