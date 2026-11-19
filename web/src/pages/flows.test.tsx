import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AuthGate } from '../app/router';
import { OnboardingPage } from './onboarding';
import { SettingsPage } from './settings';
import { account, brain, job, now, provider, setup, tasks } from '../test/fixtures';
import { json, mockApi, renderApp } from '../test/helpers';
import { LOGIN_REQUIRED } from '../api/client';

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });
describe('login', () => {
  it('shows login only when requested, signs in, and clears protected data on expiry', async () => {
    const user = userEvent.setup();
    const requests = mockApi((request, path) => {
      if (path === '/api/auth/status') return json({ login_required: true, authenticated: false });
      if (path === '/api/auth/login' && request.method === 'POST') return json({ login_required: true, authenticated: true });
    });
    const { client } = renderApp(<AuthGate><div>Protected study</div></AuthGate>);
    expect(await screen.findByRole('heading', { name: 'Welcome to your study.' })).toBeInTheDocument();
    expect(requests).toHaveLength(1);
    await user.type(screen.getByLabelText('Password'), 'test-password');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByText('Protected study')).toBeInTheDocument();
    client.setQueryData(['tokens'], ['protected-data']);
    act(() => window.dispatchEvent(new Event(LOGIN_REQUIRED)));
    expect(await screen.findByRole('heading', { name: 'Welcome to your study.' })).toBeInTheDocument();
    expect(client.getQueryData(['tokens'])).toBeUndefined();
  });
  it('skips login for an instance without a password', async () => {
    mockApi(); renderApp(<AuthGate><div>Protected study</div></AuthGate>);
    expect(await screen.findByText('Protected study')).toBeInTheDocument();
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument();
  });
});
describe('onboarding', () => {
  it('connects PAT, creates a private Brain, adds a Provider, and opens the first Source step', async () => {
    let connected = false;
    let created = false;
    const requests = mockApi((request, path) => {
      if (path === '/api/setup') return json({ ...setup, github_connected: connected, brain_count: created ? 1 : 0, github_app_configured: false });
      if (path === '/api/github/account') return json(connected ? account : null);
      if (path === '/api/github/pat') { connected = true; return json(account); }
      if (path === '/api/brains' && request.method === 'GET') return json(created ? [brain] : []);
      if (path === '/api/brains' && request.method === 'POST') { created = true; return json({ brain, job }, 201); }
      if (path === '/api/providers' && request.method === 'POST') return json(provider, 201);
    });
    const user = userEvent.setup(); renderApp(<OnboardingPage />, '/onboarding');
    expect(await screen.findByRole('heading', { name: 'Connect GitHub' })).toBeInTheDocument();
    await user.type(screen.getByLabelText('Personal access token'), 'test-pat');
    await user.click(screen.getByRole('button', { name: 'Connect' }));
    await screen.findByRole('heading', { name: 'Create a Brain' });
    await user.type(screen.getByLabelText('Brain name'), brain.name);
    await user.type(screen.getByLabelText('Repository name'), 'field-study');
    await user.click(screen.getByRole('button', { name: 'Create a Brain' }));
    await screen.findByRole('heading', { name: 'Add a Provider' });
    await user.type(screen.getByLabelText('Provider name'), provider.name);
    await user.type(screen.getByLabelText('Base URL'), provider.base_url);
    await user.click(screen.getByRole('button', { name: 'Add a Provider' }));
    expect(await screen.findByRole('heading', { name: 'Add your first Source' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Choose a file' })).toBeEnabled();
    expect(screen.getByRole('link', { name: 'Open your Brain' })).toHaveAttribute('href', '/brains/brain-1');
    const create = requests.find(request => request.method === 'POST' && request.url.endsWith('/api/brains'));
    expect(await create?.json()).toEqual({ name: brain.name, language: 'en', new_repo_name: 'field-study', confirm_public: false });
    for (const request of requests.filter(request => request.method !== 'GET')) expect(request.headers.get('X-SBB-Client')).toBe('web');
  });
  it('requires explicit confirmation for a public existing repository and allows skipping Provider', async () => {
    let created = false;
    const requests = mockApi((request, path) => {
      if (path === '/api/setup') return json({ ...setup, brain_count: created ? 1 : 0 });
      if (path === '/api/brains' && request.method === 'GET') return json(created ? [brain] : []);
      if (path === '/api/github/repos') return json([{ full_name: 'reader/public-study', private: false, default_branch: 'main', brain_id: null }]);
      if (path === '/api/brains' && request.method === 'POST') { created = true; return json({ brain, job }, 201); }
    });
    const user = userEvent.setup(); renderApp(<OnboardingPage />, '/onboarding');
    await screen.findByLabelText('Brain name');
    await user.selectOptions(screen.getByLabelText('Repository', { selector: 'select' }), 'existing');
    await screen.findByRole('option', { name: 'reader/public-study (Public)' });
    const selectors = screen.getAllByLabelText('Repository', { selector: 'select' });
    await user.selectOptions(selectors[1]!, 'reader/public-study');
    await user.type(screen.getByLabelText('Brain name'), brain.name);
    expect(screen.getByRole('button', { name: 'Create a Brain' })).toBeDisabled();
    await user.click(screen.getByLabelText('I understand that this Brain will be public.'));
    await user.click(screen.getByRole('button', { name: 'Create a Brain' }));
    await user.click(await screen.findByRole('button', { name: 'Skip for now' }));
    expect(await screen.findByRole('heading', { name: 'Add your first Source' })).toBeInTheDocument();
    const create = requests.find(request => request.method === 'POST' && request.url.endsWith('/api/brains'));
    expect(await create?.json()).toMatchObject({ existing_repo: 'reader/public-study', confirm_public: true });
  });
});
describe('Settings', () => {
  it('switches themes, tests a masked Provider, saves task settings, and reveals a token once', async () => {
    const requests = mockApi((request, path) => {
      if (path === '/api/providers/1/test') return json({ ok: true, message: 'Connection ready', model_count: 1 });
      if (path === '/api/settings/tasks' && request.method === 'PUT') return json(tasks);
      if (path === '/api/tokens' && request.method === 'POST') return json({ token: { id: 1, name: 'Study agent', prefix: 'sbb_test', created_at: now, last_used_at: null }, secret: 'sbb_one-time-test-token' }, 201);
    });
    const user = userEvent.setup(); const { client } = renderApp(<SettingsPage />);
    await screen.findByText('Local study', { selector: 'strong' });
    expect(screen.getByText('Stored key: ••••1234')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Ink' }));
    expect(document.documentElement).toHaveAttribute('data-theme', 'ink');
    expect(localStorage.getItem('sbb-theme')).toBe('ink');
    await user.click(screen.getByRole('button', { name: 'Test connection' }));
    expect(await screen.findByText('Connection ready')).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('Ingest'), '1');
    const models = screen.getAllByLabelText('Model', { selector: 'input' });
    await user.type(models[0]!, 'study-model');
    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(requests.some(request => request.method === 'PUT')).toBe(true));
    expect(await requests.find(request => request.method === 'PUT')?.json()).toEqual({ ingest: { mode: 'provider', provider_id: 1, model: 'study-model' }, transcription: { mode: 'local', model: null } });
    await user.type(screen.getByLabelText('Token name'), 'Study agent');
    await user.click(screen.getByRole('button', { name: 'Create token' }));
    const dialog = await screen.findByRole('dialog', { name: 'New API token' });
    expect(within(dialog).getByText('sbb_one-time-test-token')).toBeInTheDocument();
    expect(JSON.stringify(client.getQueryData(['tokens']))).not.toContain('sbb_one-time-test-token');
    await user.click(within(dialog).getByRole('button', { name: 'I saved this token' }));
    expect(screen.queryByText('sbb_one-time-test-token')).not.toBeInTheDocument();
    expect(screen.getByText('http://localhost:8484/mcp', { selector: 'code' })).toBeInTheDocument();
    expect(screen.getByText('Worker online')).toBeInTheDocument();
  });
});
