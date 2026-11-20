import { afterEach, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Schema } from '../../api/client';
import { BrainContext } from '../../app/context';
import { SOURCE_DRAG_TYPE } from '../../app/folder-drag';
import { addedSource, brain, job, source } from '../../test/fixtures';
import { json, mockApi, renderApp } from '../../test/helpers';
import { MockXHR, mockXhr } from '../../test/xhr';
import { BrainPlaceholder } from './index';

afterEach(() => vi.unstubAllGlobals());
const folderNode = (path: string, count = 0): Schema['FolderNode'] => ({ name: path, path, source_count: count, total_source_count: count, children: [] });
function renderBrain(folder = '', jobs: Schema['JobView'][] = [job], children = [folderNode('research', 1)]) {
  const requests = mockApi((_request, path) => {
    if (path.endsWith('/sources')) return json([{ ...source, folder: 'research', status: 'converting' }]);
    if (path.endsWith('/folders')) return json({ name: '', path: '', source_count: 0, total_source_count: 1, children });
    if (path === '/api/jobs') return json(jobs);
    if (path.endsWith('/move')) return json({ ...job, id: 5, kind: 'source.move', status: 'queued' }, 202);
  });
  return { requests, ...renderApp(<BrainContext.Provider value={{ brainId: brain.id, brain, events: [], connected: true }}><BrainPlaceholder /></BrainContext.Provider>, `/brains/${brain.id}${folder ? `?folder=${folder}` : ''}`) };
}

it('shows a partly ingested progress badge on the Sources list', async () => {
  const requests = mockApi((_request, path) => {
    if (path.endsWith('/sources') && !path.includes('/sources/')) return json([{ ...source, status: 'partly_ingested', parts_done: 12, parts_total: 131 }]);
  });
  renderApp(<BrainContext.Provider value={{ brainId: brain.id, brain, events: [], connected: true }}><BrainPlaceholder /></BrainContext.Provider>, `/brains/${brain.id}`);
  expect(await screen.findByText('Partly ingested 9%')).toBeInTheDocument();
  expect(requests.some(request => request.url.includes('ingest-estimate'))).toBe(false);
});

it.each([
  { status: 'ingesting', phase: 'map', label: 'Extracting Parts 40 of 128' },
  { status: 'partly_ingested', phase: 'reduce', label: 'Writing pages 120 of 450' },
])('shows map-reduce progress on Brain home: $phase', async ({ status, phase, label }) => {
  mockApi((_request, path) => {
    if (path.endsWith('/sources')) return json([{ ...source, mode: 'map_reduce', status, phase, parts_total: 128, map_parts_done: 40, reduce_groups_done: 120, reduce_groups_total: 450 }]);
  });
  renderApp(<BrainContext.Provider value={{ brainId: brain.id, brain, events: [], connected: true }}><BrainPlaceholder /></BrainContext.Provider>, `/brains/${brain.id}`);
  expect(await screen.findByText(label)).toBeInTheDocument();
});

it('filters Sources and sends quota-confirmed uploads to the selected Folder', async () => {
  mockXhr([{ status: 409, body: { code: 'quota_exceeded', message: 'Quota exceeded.', used_bytes: 8, upload_bytes: 4, quota_bytes: 10 } }, { status: 202, body: addedSource }]);
  const user = userEvent.setup();
  const { requests } = renderBrain('research');
  expect(await screen.findByRole('link', { name: 'Paper' })).toHaveAttribute('href', '/brains/brain-1/sources/paper');
  expect(screen.getByText('Converting')).toBeInTheDocument();
  expect(requests.some(request => new URL(request.url).searchParams.get('folder') === 'research')).toBe(true);
  const file = new File(['data'], 'paper.pdf');
  await user.upload(screen.getByLabelText('Choose a file'), file);
  const dialog = await screen.findByRole('dialog', { name: 'Add Source over the storage quota?' });
  expect(MockXHR.instances).toHaveLength(1);
  await user.click(within(dialog).getByRole('button', { name: 'Add Source anyway' }));
  await screen.findByText('Source added. Conversion is queued.');
  expect(MockXHR.instances[1]?.url).toContain('folder=research');
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
  await user.selectOptions(screen.getByLabelText('Folder'), 'research');
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  expect(MockXHR.instances).toHaveLength(1);
});

it('does not reuse a quota confirmation after selecting a different Folder', async () => {
  mockXhr([{ status: 409, body: { code: 'quota_exceeded', message: 'Quota exceeded.' } }]);
  const user = userEvent.setup(); renderBrain();
  await user.upload(screen.getByLabelText('Choose a file'), new File(['data'], 'paper.pdf'));
  const dialog = await screen.findByRole('dialog');
  await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
  await user.selectOptions(screen.getByLabelText('Folder'), 'research');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(MockXHR.instances).toHaveLength(1);
});

it('shows the Folder that new Sources go to', async () => {
  renderBrain('research');
  expect((await screen.findByText('New Sources go to')).closest('p')).toHaveTextContent('New Sources go to research');
  await screen.findByRole('option', { name: 'research' });
  expect(screen.queryByText(/not in the Brain yet/)).not.toBeInTheDocument();
});

it('says that the first Source creates a Folder that is not in the Brain yet', async () => {
  const user = userEvent.setup();
  renderBrain(encodeURIComponent('field-notes/2026'));
  expect((await screen.findByText('New Sources go to')).closest('p')).toHaveTextContent('New Sources go to field-notes / 2026');
  expect(await screen.findByText('This Folder is not in the Brain yet. The first Source you add creates it.')).toBeInTheDocument();
  // The Folder field shows the same Folder as the target, not the first option.
  expect(screen.getByLabelText('Folder')).toHaveValue('field-notes/2026');
  await user.selectOptions(screen.getByLabelText('Folder'), 'All Sources');
  expect((await screen.findByText('New Sources go to')).closest('p')).toHaveTextContent('New Sources go to Brain root');
});

it('holds new Sources while a Folder job changes the selected Folder', async () => {
  renderBrain('archive', [{ ...job, id: 9, kind: 'folder.rename', status: 'queued', payload: { brain_id: brain.id, folder: 'archive', new_folder: 'old' } }], [folderNode('research', 1), folderNode('archive')]);
  expect(await screen.findByText('Renaming archive to old… New Sources can go in when the job ends.')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Choose a file' })).toBeDisabled();
});

it('takes no new Source into a Folder whose name is not a slug', async () => {
  const user = userEvent.setup();
  renderBrain(encodeURIComponent('Field Notes'), [job], [folderNode('research', 1), folderNode('Field Notes', 2)]);
  expect(await screen.findByText(/New Sources cannot go into Field Notes/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Choose a file' })).toBeDisabled();
  await user.click(await screen.findByRole('button', { name: 'Move Paper to another Folder' }));
  const dialog = await screen.findByRole('dialog', { name: 'Move Paper' });
  expect(within(dialog).getByRole('option', { name: 'Field Notes (rename it first)' })).toBeDisabled();
});

it('does not offer the upload zone as a target for a dragged Source row', async () => {
  renderBrain();
  const zone = (await screen.findByText('Drop anything')).closest('.drop-zone')!;
  fireEvent.dragOver(zone, { dataTransfer: { types: [SOURCE_DRAG_TYPE, 'text/plain'], dropEffect: 'none' } });
  expect(zone).not.toHaveClass('dragging');
  fireEvent.dragOver(zone, { dataTransfer: { types: ['Files'], dropEffect: 'none' } });
  expect(zone).toHaveClass('dragging');
});

it('lets a Source be dragged to a sidebar Folder, or moved with the keyboard', async () => {
  const user = userEvent.setup();
  const { requests } = renderBrain();
  const row = (await screen.findByRole('link', { name: 'Paper' })).closest('li')!;
  const setData = vi.fn();
  fireEvent.dragStart(row, { dataTransfer: { setData, effectAllowed: 'all' } });
  expect(setData).toHaveBeenCalledWith(SOURCE_DRAG_TYPE, JSON.stringify({ brainId: brain.id, slug: 'paper', folder: 'research', title: 'Paper' }));
  expect(row).toHaveClass('is-dragging');
  fireEvent.dragEnd(row);
  expect(row).not.toHaveClass('is-dragging');

  const moveButton = within(row).getByRole('button', { name: 'Move Paper to another Folder' });
  await user.click(moveButton);
  await user.click(within(await screen.findByRole('dialog', { name: 'Move Paper' })).getByRole('button', { name: 'Cancel' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(moveButton).toHaveFocus();
  await user.click(moveButton);
  const dialog = await screen.findByRole('dialog', { name: 'Move Paper' });
  const move = within(dialog).getByRole('button', { name: 'Move' });
  expect(move).toBeDisabled();
  await user.selectOptions(within(dialog).getByLabelText('Folder'), 'Brain root');
  await user.click(move);
  await waitFor(() => expect(requests.filter(request => request.method === 'POST')).toHaveLength(1));
  const [post] = requests.filter(request => request.method === 'POST');
  expect(new URL(post!.url).pathname).toBe('/api/brains/brain-1/sources/paper/move');
  expect(await post!.json()).toEqual({ folder: '' });
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(screen.getByRole('link', { name: 'Paper' })).toHaveFocus();
});

it('offers no Folder that a pending Folder job changes in the Move dialog', async () => {
  const user = userEvent.setup();
  renderBrain('', [{ ...job, id: 9, kind: 'folder.delete', status: 'queued', payload: { brain_id: brain.id, folder: 'archive' } }], [folderNode('research', 1), folderNode('archive')]);
  await user.click(await screen.findByRole('button', { name: 'Move Paper to another Folder' }));
  const dialog = await screen.findByRole('dialog', { name: 'Move Paper' });
  await waitFor(() => expect(within(dialog).getByRole('option', { name: 'archive (wait for its Folder job)' })).toBeDisabled());
  expect(within(dialog).getByRole('option', { name: 'research' })).toBeEnabled();
});

it('shows a pending move on the Source row and locks it', async () => {
  renderBrain('', [{ ...job, id: 6, kind: 'source.move', status: 'queued', payload: { brain_id: brain.id, source_slug: 'paper', folder: 'archive' } }]);
  expect(await screen.findByText('Moving to archive…')).toBeInTheDocument();
  const row = screen.getByRole('link', { name: 'Paper' }).closest('li')!;
  expect(row).toHaveAttribute('draggable', 'false');
  expect(within(row).getByRole('button', { name: 'Move Paper to another Folder' })).toBeDisabled();
});
