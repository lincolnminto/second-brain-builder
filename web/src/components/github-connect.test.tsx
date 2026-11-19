import { afterEach, expect, it, vi } from 'vitest';
import { act, fireEvent, screen } from '@testing-library/react';
import { GitHubConnect } from './github-connect';
import { account } from '../test/fixtures';
import { json, mockApi, renderApp } from '../test/helpers';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
it('polls at the server interval, respects slow_down, and stops after authorization', async () => {
  vi.useFakeTimers();
  let polls = 0;
  let connected = false;
  const requests = mockApi((_request, path) => {
    if (path === '/api/github/account') return json(connected ? account : null);
    if (path === '/api/github/device/start') return json({ user_code: 'STUD-Y123', verification_uri: 'https://github.com/login/device', expires_in: 60, interval: 5 });
    if (path === '/api/github/device/poll') {
      polls++;
      if (polls === 1) return json({ status: 'slow_down', interval: 10, account: null });
      connected = true; return json({ status: 'connected', interval: null, account });
    }
  });
  const onConnected = vi.fn(); const { unmount } = renderApp(<GitHubConnect onConnected={onConnected} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  fireEvent.click(screen.getByRole('button', { name: 'Connect with GitHub' }));
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(screen.getByText('STUD-Y123')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Open GitHub' })).toHaveAttribute('href', 'https://github.com/login/device');
  await act(async () => { await vi.advanceTimersByTimeAsync(4999); });
  expect(polls).toBe(0);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(polls).toBe(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(9999); });
  expect(polls).toBe(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(polls).toBe(2);
  expect(onConnected).toHaveBeenCalledOnce();
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(polls).toBe(2);
  expect(requests.filter(request => request.url.includes('device/poll')).every(request => request.headers.get('X-SBB-Client') === 'web')).toBe(true);
  unmount();
});
it('stops polling when the device code expires', async () => {
  vi.useFakeTimers();
  let polls = 0;
  mockApi((_request, path) => {
    if (path === '/api/github/account') return json(null);
    if (path === '/api/github/device/start') return json({ user_code: 'STUD-Y123', verification_uri: 'https://github.com/login/device', expires_in: 1, interval: 2 });
    if (path === '/api/github/device/poll') { polls++; return json({ status: 'pending', interval: 2 }); }
  });
  renderApp(<GitHubConnect />);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  fireEvent.click(screen.getByRole('button', { name: 'Connect with GitHub' }));
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(screen.getByRole('alert')).toHaveTextContent('This code expired. Start again to get a new code.');
  expect(polls).toBe(0);
});
