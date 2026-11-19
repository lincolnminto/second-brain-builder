import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClientProvider } from '@tanstack/react-query';
import '@fontsource/geist/400.css';
import '@fontsource/geist/500.css';
import '@fontsource/geist/600.css';
import '@fontsource/geist-mono/400.css';
import '@fontsource/newsreader/400.css';
import '@fontsource/newsreader/500.css';
import { queryClient } from './api/query-client';
import { AppRouter } from './app/router';
import { ThemeProvider } from './theme/provider';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><QueryClientProvider client={queryClient}><ThemeProvider><AppRouter /></ThemeProvider></QueryClientProvider></React.StrictMode>,
);
