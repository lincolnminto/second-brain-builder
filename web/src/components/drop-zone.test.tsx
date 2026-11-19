import { afterEach, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DropZone } from './drop-zone';
import { addedSource, source } from '../test/fixtures';
import { renderApp } from '../test/helpers';
import { mockXhr, MockXHR } from '../test/xhr';
import { uploadSource } from '../api/upload';
import { LOGIN_REQUIRED } from '../api/client';

afterEach(() => vi.unstubAllGlobals());
it('uploads multipart with progress and client header', async () => {
  mockXhr([{ status: 202, body: addedSource }]);
  const onProgress = vi.fn();
  const file = new File(['data'], 'paper.pdf', { type: 'application/pdf' });
  expect(await uploadSource({ brainId: 'brain-1', file, onProgress })).toEqual(addedSource);
  expect(onProgress).toHaveBeenCalledWith(50);
  expect(MockXHR.instances[0]?.headers.get('X-SBB-Client')).toBe('web');
  expect(MockXHR.instances[0]?.headers.has('Content-Type')).toBe(false);
  expect(MockXHR.instances[0]?.body?.get('file')).toBe(file);
});
it('requires quota confirmation before retrying the same file', async () => {
  mockXhr([
    { status: 409, body: { code: 'quota_exceeded', message: 'Quota exceeded.', used_bytes: 8, upload_bytes: 4, quota_bytes: 10 } },
    { status: 202, body: addedSource },
  ]);
  const user = userEvent.setup(); const onAdded = vi.fn();
  renderApp(<DropZone brainId="brain-1" onAdded={onAdded} />);
  await user.upload(screen.getByLabelText('Choose a file'), new File(['data'], 'paper.pdf'));
  const dialog = await screen.findByRole('dialog', { name: 'Add Source over the storage quota?' });
  expect(MockXHR.instances).toHaveLength(1);
  await user.click(within(dialog).getByRole('button', { name: 'Add Source anyway' }));
  expect(await screen.findByText('Source added. Conversion is queued.')).toBeInTheDocument();
  expect(MockXHR.instances[1]?.url).toContain('confirm_over_quota=true');
  expect(onAdded).toHaveBeenCalledOnce();
});
it('links a duplicate Source and never silently retries', async () => {
  mockXhr([{ status: 409, body: { code: 'duplicate_source', message: 'Duplicate.', existing_source: source } }]);
  const user = userEvent.setup(); renderApp(<DropZone brainId="brain-1" />);
  await user.upload(screen.getByLabelText('Choose a file'), new File(['data'], 'paper.pdf'));
  expect(await screen.findByRole('link', { name: 'Open existing Source' })).toHaveAttribute('href', '/brains/brain-1/sources/paper');
  expect(MockXHR.instances).toHaveLength(1);
});
it('propagates login expiry from the upload transport', async () => {
  mockXhr([{ status: 401, body: { code: 'login_required', message: 'Sign in.' } }]);
  const listener = vi.fn(); window.addEventListener(LOGIN_REQUIRED, listener);
  await expect(uploadSource({ brainId: 'brain-1', file: new File(['data'], 'paper.pdf') })).rejects.toMatchObject({ status: 401 });
  await waitFor(() => expect(listener).toHaveBeenCalledOnce());
  window.removeEventListener(LOGIN_REQUIRED, listener);
});
