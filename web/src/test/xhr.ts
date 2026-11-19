import { vi } from 'vitest';
export type XhrReply = { status: number; body: unknown };
export class MockXHR {
  static replies: XhrReply[] = [];
  static instances: MockXHR[] = [];
  headers = new Headers();
  method = ''; url = ''; body?: FormData;
  status = 0; statusText = ''; response: unknown; responseType = '';
  upload: { onprogress: ((event: { loaded: number; total: number; lengthComputable: boolean }) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  constructor() { MockXHR.instances.push(this); }
  open(method: string, url: string) { this.method = method; this.url = url; }
  setRequestHeader(key: string, value: string) { this.headers.set(key, value); }
  send(body: FormData) {
    this.body = body;
    const reply = MockXHR.replies.shift();
    if (!reply) throw new Error('Missing XHR reply');
    queueMicrotask(() => {
      this.upload.onprogress?.({ loaded: 2, total: 4, lengthComputable: true });
      this.status = reply.status; this.response = reply.body; this.onload?.();
    });
  }
  abort() { this.onabort?.(); }
}
export function mockXhr(replies: XhrReply[]) {
  MockXHR.replies = [...replies]; MockXHR.instances = [];
  vi.stubGlobal('XMLHttpRequest', MockXHR);
}
