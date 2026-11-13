import { createRoot } from 'react-dom/client';
import { ModelSettingsView } from '../src/views/ModelSettings';
import { I18nProvider } from '../src/i18n/I18nProvider';
import '../src/styles.css';
createRoot(document.getElementById('root')!).render(<I18nProvider><main className="shell"><ModelSettingsView /></main></I18nProvider>);
