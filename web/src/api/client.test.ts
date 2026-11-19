import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, fileUrl, LOGIN_REQUIRED, originalUrl, result } from './client';
import { json, mockApi } from '../test/helpers';

afterEach(() => vi.unstubAllGlobals());
describe('HTTP contract and security', () => {
  it('sends the client header on writes and preserves a typed body', async () => {
    const requests = mockApi(() => json({ login_required: true, authenticated: true }));
    await result(api.POST('/api/auth/login', { body: { password: 'test-password' } }));
    expect(requests[0]?.headers.get('X-SBB-Client')).toBe('web');
    expect(await requests[0]?.json()).toEqual({ password: 'test-password' });
    expect(requests[0]?.credentials).toBe('same-origin');
    await result(api.GET('/api/auth/status'));
    expect(requests[1]?.headers.has('X-SBB-Client')).toBe(false);
  });
  it('redirects only for the structured login_required error', async () => {
    const listener = vi.fn();
    window.addEventListener(LOGIN_REQUIRED, listener);
    mockApi(() => json({ code: 'auth', message: 'GitHub needs attention.' }, 401));
    await expect(result(api.GET('/api/setup'))).rejects.toBeInstanceOf(ApiError);
    expect(listener).not.toHaveBeenCalled();
    mockApi(() => json({ code: 'login_required', message: 'Please sign in.' }, 401));
    await expect(result(api.GET('/api/setup'))).rejects.toMatchObject({ status: 401, body: { code: 'login_required' } });
    expect(listener).toHaveBeenCalledOnce();
    window.removeEventListener(LOGIN_REQUIRED, listener);
  });
  it('supports empty 204 responses and encodes file URLs', async () => {
    mockApi(() => new Response(null, { status: 204 }));
    expect(await result(api.DELETE('/api/tokens/{token_id}', { params: { path: { token_id: 3 } } }))).toBeUndefined();
    expect(fileUrl('a b', 'sources/a b/assets/chart.png')).toBe('/api/brains/a%20b/files/sources/a%20b/assets/chart.png');
    expect(originalUrl('brain', 'a b')).toBe('/api/brains/brain/sources/a%20b/original');
  });
});
