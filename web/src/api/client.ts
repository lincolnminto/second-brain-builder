import createClient, { type Middleware } from 'openapi-fetch';
import type { components, paths } from './schema';

export type Schema = components['schemas'];
export type ApiErrorBody = Schema['ErrorResponse'];
export const LOGIN_REQUIRED = 'sbb:login-required';

export class ApiError extends Error {
  constructor(public readonly status: number, public readonly body: ApiErrorBody) {
    super(body.message);
    this.name = 'ApiError';
  }
}

export function isErrorBody(value: unknown): value is ApiErrorBody {
  return typeof value === 'object' && value !== null && 'code' in value &&
    typeof value.code === 'string' && 'message' in value && typeof value.message === 'string';
}

export function announceLogin(status: number, body: ApiErrorBody) {
  if (status === 401 && body.code === 'login_required') {
    window.dispatchEvent(new Event(LOGIN_REQUIRED));
  }
}

export const securityMiddleware: Middleware = {
  onRequest({ request }) {
    if (request.method !== 'GET') request.headers.set('X-SBB-Client', 'web');
    return request;
  },
  async onResponse({ response }) {
    if (!response.ok) {
      const value: unknown = await response.clone().json().catch(() => null);
      const body = isErrorBody(value) ? value : { code: 'http_error', message: response.statusText || `HTTP ${response.status}` };
      announceLogin(response.status, body);
      throw new ApiError(response.status, body);
    }
    return response;
  },
};

export const api = createClient<paths>({ baseUrl: window.location.origin, credentials: 'same-origin', fetch: request => globalThis.fetch(request) });
api.use(securityMiddleware);

export async function result<T>(request: Promise<{ data?: T; error?: unknown }>): Promise<T> {
  const response = await request;
  if (response.error) throw response.error;
  return response.data as T;
}

export function fileUrl(brainId: string, path: string) {
  return `/api/brains/${encodeURIComponent(brainId)}/files/${path.split('/').map(encodeURIComponent).join('/')}`;
}
export function originalUrl(brainId: string, slug: string) {
  return `/api/brains/${encodeURIComponent(brainId)}/sources/${encodeURIComponent(slug)}/original`;
}
