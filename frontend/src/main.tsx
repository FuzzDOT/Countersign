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
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </Providers>
  </StrictMode>,
);
