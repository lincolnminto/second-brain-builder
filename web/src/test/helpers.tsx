import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { vi } from 'vitest';
import { ThemeProvider } from '../theme/provider';
import { account, agentSetup, brain, job, provider, setup, tasks, worker } from './fixtures';

export function renderApp(ui: ReactNode, route = '/') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false, gcTime: 0 } } });
  return { client, ...render(<QueryClientProvider client={client}><ThemeProvider><MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter></ThemeProvider></QueryClientProvider>) };
}
export type Handler = (request: Request, path: string) => Promise<Response | undefined> | Response | undefined;
export function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } }); }
export function mockApi(handler?: Handler) {
  const requests: Request[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const request = input instanceof Request ? input : new Request(input);
    requests.push(request.clone());
    const path = new URL(request.url).pathname;
    const custom = await handler?.(request.clone(), path);
    if (custom) return custom;
    const defaults: Record<string, unknown> = {
      '/api/auth/status': { login_required: false, authenticated: true },
      '/api/setup': setup, '/api/brains': [brain], '/api/github/account': account,
      '/api/github/repos': [], '/api/github/install-url': { url: 'https://github.com/apps/sbb/installations/new' },
      '/api/providers': [provider], '/api/providers/1/models': ['study-model'],
      '/api/settings/tasks': tasks, '/api/tokens': [], '/api/agent-setup': agentSetup,
      '/api/health': { ok: true, database: true, worker }, '/api/jobs': [job],
      '/api/brains/brain-1/folders': { name: '', path: '', source_count: 0, total_source_count: 0, children: [] },
      '/api/brains/brain-1/search': [],
    };
    if (!(path in defaults)) throw new Error(`Unexpected API call: ${request.method} ${path}`);
    return json(defaults[path]);
  }));
  return requests;
}
