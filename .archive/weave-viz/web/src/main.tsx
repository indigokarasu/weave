import React from 'react';
import { createRoot } from 'react-dom/client';
import App, { ErrorBoundary } from './App';
import './styles/globals.css';

console.log('[Weave] main.tsx loading...');

const root = document.getElementById('root');
if (root) {
  console.log('[Weave] root element found, rendering App');
  createRoot(root).render(
    <React.StrictMode>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </React.StrictMode>
  );
  console.log('[Weave] render called');
} else {
  console.error('[Weave] root element NOT found');
}
