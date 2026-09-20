import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { Providers } from './app/providers';
import './styles/globals.css';

const root = document.getElementById('root');
if (!root) throw new Error('Missing #root element');

// Providers = TanStack Query + auth session + toasts (shared layer).
createRoot(root).render(
  <StrictMode>
    <Providers>
      {/* Opting in to the v7 behaviours we already rely on, which also
          silences the two upgrade warnings React Router logs on every
          boot — console noise that hides real errors during a demo. */}
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <App />
      </BrowserRouter>
    </Providers>
  </StrictMode>,
);
