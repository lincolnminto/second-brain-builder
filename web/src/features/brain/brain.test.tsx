import { afterEach, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrainContext } from '../../app/context';
import { addedSource, brain, source } from '../../test/fixtures';
import { json, mockApi, renderApp } from '../../test/helpers';
import { MockXHR, mockXhr } from '../../test/xhr';
import { BrainPlaceholder } from './index';

afterEach(() => vi.unstubAllGlobals());
function renderBrain(folder = '') {
  const requests = mockApi((_request, path) => {
    if (path.endsWith('/sources')) return json([{ ...source, folder: 'Research', status: 'converting' }]);
    if (path.endsWith('/folders')) return json({ name: '', path: '', source_count: 0, total_source_count: 1, children: [{ name: 'Research', path: 'Research', source_count: 1, total_source_count: 1 }] });
  });
  return { requests, ...renderApp(<BrainContext.Provider value={{ brainId: brain.id, brain, events: [], connected: true }}><BrainPlaceholder /></BrainContext.Provider>, `/brains/${brain.id}${folder ? `?folder=${folder}` : ''}`) };
}

it('filters Sources and sends quota-confirmed uploads to the selected Folder', async () => {
  mockXhr([{ status: 409, body: { code: 'quota_exceeded', message: 'Quota exceeded.', used_bytes: 8, upload_bytes: 4, quota_bytes: 10 } }, { status: 202, body: addedSource }]);
  const user = userEvent.setup();
  const { requests } = renderBrain('Research');
  expect(await screen.findByRole('link', { name: 'Paper' })).toHaveAttribute('href', '/brains/brain-1/sources/paper');
  expect(screen.getByText('Converting')).toBeInTheDocument();
  expect(requests.some(request => new URL(request.url).searchParams.get('folder') === 'Research')).toBe(true);
  const file = new File(['data'], 'paper.pdf');
  await user.upload(screen.getByLabelText('Choose a file'), file);
  const dialog = await screen.findByRole('dialog', { name: 'Add Source over the storage quota?' });
  expect(MockXHR.instances).toHaveLength(1);
  await user.click(within(dialog).getByRole('button', { name: 'Add Source anyway' }));
  await screen.findByText('Source added. Conversion is queued.');
  expect(MockXHR.instances[1]?.url).toContain('folder=Research');
  expect(MockXHR.instances[1]?.url).toContain('confirm_over_quota=true');
  expect(MockXHR.instances[1]?.body?.get('file')).toBe(file);
});

it('links a duplicate Source without retrying or adding another Source', async () => {
  mockXhr([{ status: 409, body: { code: 'duplicate_source', message: 'Duplicate.', existing_source: source } }]);
  const user = userEvent.setup(); renderBrain();
  await user.upload(screen.getByLabelText('Choose a file'), new File(['data'], 'paper.pdf'));
  expect(await screen.findByRole('link', { name: 'Open existing Source' })).toHaveAttribute('href', '/brains/brain-1/sources/paper');
  expect(MockXHR.instances).toHaveLength(1);
});

it('shows upload failures and clears the pending file when the Folder changes', async () => {
  mockXhr([{ status: 415, body: { code: 'unsupported_format', message: 'This format is not supported.' } }]);
  const user = userEvent.setup(); renderBrain();
  await user.upload(screen.getByLabelText('Choose a file'), new File(['data'], 'paper.bin'));
  expect(await screen.findByRole('alert')).toHaveTextContent('This format is not supported.');
  await user.selectOptions(screen.getByLabelText('Folder'), 'Research');
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  expect(MockXHR.instances).toHaveLength(1);
});

it('does not reuse a quota confirmation after selecting a different Folder', async () => {
  mockXhr([{ status: 409, body: { code: 'quota_exceeded', message: 'Quota exceeded.' } }]);
  const user = userEvent.setup(); renderBrain();
  await user.upload(screen.getByLabelText('Choose a file'), new File(['data'], 'paper.pdf'));
  const dialog = await screen.findByRole('dialog');
  await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
  await user.selectOptions(screen.getByLabelText('Folder'), 'Research');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(MockXHR.instances).toHaveLength(1);
});
