import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { HashRouter } from 'react-router-dom';
import App from './App';
import { ToastProvider } from './components/ui';
import { ErrorBoundary } from './components/ErrorBoundary';
import './styles/tokens.css';
import './styles/base.css';
import './styles/components.css';

// HashRouter : fonctionne identique dans le navigateur, sous Electron (file://) et derrière le backend.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <HashRouter>
      <ToastProvider>
        <ErrorBoundary full>
          <App />
        </ErrorBoundary>
      </ToastProvider>
    </HashRouter>
  </StrictMode>,
);
