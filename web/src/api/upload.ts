import { t } from '../i18n/en';
import { ApiError, announceLogin, isErrorBody, type Schema } from './client';

export type UploadOptions = {
  brainId: string; file: File; folder?: string; confirmOverQuota?: boolean;
  onProgress?: (percent: number) => void; signal?: AbortSignal;
};

// XHR is used only for multipart upload progress, which fetch does not expose.
export function uploadSource({ brainId, file, folder = '', confirmOverQuota = false, onProgress, signal }: UploadOptions): Promise<Schema['AddSourceResult']> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const query = new URLSearchParams({ folder, confirm_over_quota: String(confirmOverQuota) });
    xhr.open('POST', `/api/brains/${encodeURIComponent(brainId)}/sources?${query}`);
    xhr.setRequestHeader('X-SBB-Client', 'web');
    xhr.responseType = 'json';
    const abort = () => xhr.abort();
    const cleanup = () => signal?.removeEventListener('abort', abort);
    xhr.upload.onprogress = (event) => { if (event.lengthComputable) onProgress?.(Math.round(event.loaded / event.total * 100)); };
    xhr.onerror = () => { cleanup(); reject(new Error(t('uploadFailed'))); };
    xhr.onabort = () => { cleanup(); reject(new DOMException(t('uploadCancelled'), 'AbortError')); };
    xhr.onload = () => {
      cleanup();
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(xhr.response as Schema['AddSourceResult']);
      } else {
        const body = isErrorBody(xhr.response) ? xhr.response : { code: 'upload_failed', message: xhr.statusText || t('uploadFailed') };
        announceLogin(xhr.status, body);
        reject(new ApiError(xhr.status, body));
      }
    };
    if (signal?.aborted) { reject(new DOMException(t('uploadCancelled'), 'AbortError')); return; }
    signal?.addEventListener('abort', abort, { once: true });
    const body = new FormData();
    body.append('file', file);
    xhr.send(body);
  });
}
