import { afterEach, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router';
import type { Schema } from '../api/client';
import { brain, job } from '../test/fixtures';
import { json, mockApi, renderApp, type Handler } from '../test/helpers';
import { SOURCE_DRAG_TYPE } from './folder-drag';
import { FolderTree } from './folder-tree';

afterEach(() => vi.unstubAllGlobals());

const tree: Schema['FolderNode'] = { name: '', path: '', source_count: 0, total_source_count: 3, children: [
  { name: 'archive', path: 'archive', source_count: 0, total_source_count: 0, children: [
    { name: '2025', path: 'archive/2025', source_count: 0, total_source_count: 0, children: [] },
  ] },
  { name: 'work', path: 'work', source_count: 1, total_source_count: 3, children: [
    { name: 'clients', path: 'work/clients', source_count: 2, total_source_count: 2, children: [] },
  ] },
] };
const folderJob = (id: number, kind: Schema['JobKind'], payload: Record<string, unknown>, status: Schema['JobStatus'] = 'queued', error: string | null = null): Schema['JobView'] =>
  ({ ...job, id, kind, status, error, payload: { brain_id: brain.id, ...payload } });

function Search() {
  return <span data-testid="search">{useLocation().search}</span>;
}

function renderTree({ route = `/brains/${brain.id}`, jobs = [] as Schema['JobView'][], after = (): Schema['JobView'][] | undefined => undefined, folders = tree, handle = (() => undefined) as Handler } = {}) {
  let currentJobs = jobs;
  const requests = mockApi(async (request, path) => {
    const custom = await handle(request, path);
    if (custom) return custom;
    if (path === '/api/brains/brain-1/folders' && request.method === 'GET') return json(folders);
    if (path === '/api/jobs') return json(currentJobs);
    if (request.method !== 'GET') {
      const queued = folderJob(20, 'folder.create', {});
      currentJobs = after() ?? currentJobs;
      return json(queued, 202);
    }
  });
  return { requests, ...renderApp(<><FolderTree brainId={brain.id} onNavigate={() => undefined} /><Search /></>, route) };
}
const sent = (requests: Request[], method: string) => requests.filter(request => request.method === method);
/** A request that waits until the test answers it. */
function later() {
  let answer: (response: Response) => void = () => undefined;
  const response = new Promise<Response>(resolve => { answer = resolve; });
  return { response, answer: (value: Response) => answer(value) };
}

it('previews the slug path of a new Folder and creates it from the keyboard', async () => {
  const user = userEvent.setup();
  const { requests } = renderTree();
  await screen.findByRole('link', { name: 'archive' });
  await user.click(screen.getByRole('button', { name: 'New Folder' }));
  const input = screen.getByRole('textbox', { name: 'Name of the new Folder' });
  expect(input).toHaveFocus();
  await user.type(input, 'Client Notes');
  expect(screen.getByText('Will be saved as sources/client-notes/')).toBeInTheDocument();
  await user.clear(input);
  await user.type(input, 'Archive');
  expect(screen.getByText('A Folder with this name exists already.')).toBeInTheDocument();
  expect(input).toHaveAttribute('aria-invalid', 'true');
  expect(screen.getByRole('button', { name: 'Create' })).toBeDisabled();
  await user.keyboard('{Enter}');
  expect(sent(requests, 'POST')).toHaveLength(0);
  await user.clear(input);
  await user.type(input, 'Field Trips{Enter}');
  await waitFor(() => expect(sent(requests, 'POST')).toHaveLength(1));
  const [post] = sent(requests, 'POST');
  expect(new URL(post!.url).pathname).toBe('/api/brains/brain-1/folders');
  expect(await post!.json()).toEqual({ folder: 'field-trips' });
  await waitFor(() => expect(screen.queryByRole('textbox')).not.toBeInTheDocument());
  expect(screen.getByRole('button', { name: 'New Folder' })).toHaveFocus();
  expect(screen.getByRole('status')).toHaveTextContent('Creating the Folder field-trips.');
});

it('opens the row menu with the keyboard and creates a subfolder', async () => {
  const user = userEvent.setup();
  const { requests } = renderTree();
  const trigger = await screen.findByRole('button', { name: 'Actions for work' });
  trigger.focus();
  await user.keyboard('{Enter}');
  const menu = screen.getByRole('menu', { name: 'Actions for work' });
  expect(within(menu).getByRole('menuitem', { name: 'New Folder inside' })).toHaveFocus();
  await user.keyboard('{ArrowDown}');
  expect(within(menu).getByRole('menuitem', { name: 'Rename' })).toHaveFocus();
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
  await user.keyboard('{ArrowDown}{Enter}');
  const input = screen.getByRole('textbox', { name: 'Name of the new Folder in work' });
  await user.type(input, 'Notes 2026');
  expect(screen.getByText('Will be saved as sources/work/notes-2026/')).toBeInTheDocument();
  await user.keyboard('{Enter}');
  await waitFor(() => expect(sent(requests, 'POST')).toHaveLength(1));
  expect(await sent(requests, 'POST')[0]!.json()).toEqual({ folder: 'work/notes-2026' });
});

it('deletes only a Folder without Sources', async () => {
  const user = userEvent.setup();
  const { requests } = renderTree();
  await user.click(await screen.findByRole('button', { name: 'Actions for work' }));
  const blocked = screen.getByRole('menuitem', { name: 'Delete' });
  expect(blocked).toHaveAttribute('aria-disabled', 'true');
  expect(blocked).toHaveAccessibleDescription('Move or delete its 3 Sources first.');
  await user.click(blocked);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  const archive = screen.getByRole('link', { name: 'archive' });
  await user.click(screen.getByRole('button', { name: 'Actions for archive' }));
  await user.click(screen.getByRole('menuitem', { name: 'Delete' }));
  await screen.findByRole('dialog', { name: 'Delete the Folder archive?' });
  await user.keyboard('{Escape}');
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(archive).toHaveFocus();
  await user.click(screen.getByRole('button', { name: 'Actions for archive' }));
  await user.click(screen.getByRole('menuitem', { name: 'Delete' }));
  const dialog = await screen.findByRole('dialog', { name: 'Delete the Folder archive?' });
  await user.click(within(dialog).getByRole('button', { name: 'Delete' }));
  await waitFor(() => expect(sent(requests, 'DELETE')).toHaveLength(1));
  const url = new URL(sent(requests, 'DELETE')[0]!.url);
  expect(url.pathname).toBe('/api/brains/brain-1/folders');
  expect(url.searchParams.get('folder')).toBe('archive');
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(archive).toHaveFocus();
});

function dragData(source: { slug: string; folder: string; brainId?: string; title?: string }) {
  const value = JSON.stringify({ brainId: brain.id, title: 'Paper', ...source });
  return { types: [SOURCE_DRAG_TYPE, 'text/plain'], getData: (type: string) => type === SOURCE_DRAG_TYPE ? value : '', dropEffect: 'none' };
}

it('moves a Source that is dropped on a Folder', async () => {
  const { requests } = renderTree();
  const row = (await screen.findByRole('link', { name: 'clients' })).closest('.folder-row')!;
  const dataTransfer = dragData({ slug: 'paper', folder: 'work' });
  fireEvent.dragOver(row, { dataTransfer });
  expect(row).toHaveClass('is-drop-target');
  fireEvent.drop(row, { dataTransfer });
  expect(row).not.toHaveClass('is-drop-target');
  await waitFor(() => expect(sent(requests, 'POST')).toHaveLength(1));
  const [post] = sent(requests, 'POST');
  expect(new URL(post!.url).pathname).toBe('/api/brains/brain-1/sources/paper/move');
  expect(await post!.json()).toEqual({ folder: 'work/clients' });
  expect(await screen.findByRole('status')).toHaveTextContent('Moving Paper to work/clients.');

  const work = screen.getByRole('link', { name: 'work' }).closest('.folder-row')!;
  fireEvent.drop(work, { dataTransfer: dragData({ slug: 'paper', folder: 'work' }) });
  expect(screen.getByRole('status')).toHaveTextContent('Paper is in this Folder already.');
  const rootRow = screen.getByRole('link', { name: 'All Sources' }).closest('.folder-row')!;
  fireEvent.dragOver(rootRow, { dataTransfer: dragData({ slug: 'notes', folder: 'work' }) });
  expect(rootRow).toHaveTextContent('Move to Brain root');
  fireEvent.drop(rootRow, { dataTransfer: dragData({ slug: 'notes', folder: 'work' }) });
  expect(rootRow).not.toHaveTextContent('Move to Brain root');
  await waitFor(() => expect(sent(requests, 'POST')).toHaveLength(2));
  expect(await sent(requests, 'POST')[1]!.json()).toEqual({ folder: '' });
  fireEvent.drop(work, { dataTransfer: { types: ['Files'], getData: () => '' } });
  expect(sent(requests, 'POST')).toHaveLength(2);
});

it('shows pending Folder jobs on their rows and blocks changes until they end', async () => {
  const user = userEvent.setup();
  renderTree({ jobs: [folderJob(7, 'folder.create', { folder: 'drafts' }), folderJob(8, 'folder.rename', { folder: 'work', new_folder: 'projects' }, 'running')] });
  const drafts = (await screen.findByText('drafts')).closest('.folder-row')!;
  expect(drafts).toHaveTextContent('Creating…');
  expect(within(drafts as HTMLElement).queryByRole('link')).not.toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'work' }).closest('.folder-row')).toHaveTextContent('Renaming to projects…');
  await user.click(screen.getByRole('button', { name: 'Actions for clients' }));
  expect(screen.getByRole('menuitem', { name: 'Rename' })).toHaveAccessibleDescription('Wait until the Folder job ends.');
  expect(screen.getByRole('menuitem', { name: 'Rename' })).toHaveAttribute('aria-disabled', 'true');
});

it('renames the selected Folder and follows it when the job ends', async () => {
  const user = userEvent.setup();
  const { requests } = renderTree({
    route: `/brains/${brain.id}?folder=work%2Fclients`,
    after: () => [folderJob(20, 'folder.rename', { folder: 'work', new_folder: 'projects' }, 'succeeded')],
  });
  expect(await screen.findByRole('link', { name: 'clients' })).toHaveAttribute('aria-current', 'page');
  await user.click(screen.getByRole('button', { name: 'Actions for work' }));
  await user.click(screen.getByRole('menuitem', { name: 'Rename' }));
  const input = screen.getByRole('textbox', { name: 'New name for work' });
  expect(input).toHaveValue('work');
  expect(screen.getByText('The Folder has this name already.')).toBeInTheDocument();
  await user.clear(input);
  await user.type(input, 'Projects{Enter}');
  await waitFor(() => expect(sent(requests, 'POST')).toHaveLength(1));
  const [post] = sent(requests, 'POST');
  expect(new URL(post!.url).pathname).toBe('/api/brains/brain-1/folders/rename');
  expect(await post!.json()).toEqual({ folder: 'work', new_folder: 'projects' });
  await waitFor(() => expect(new URLSearchParams(screen.getByTestId('search').textContent ?? '').get('folder')).toBe('projects/clients'));
});

it('shows a failed Folder job until it is dismissed', async () => {
  const user = userEvent.setup();
  renderTree({ after: () => [folderJob(20, 'folder.create', { folder: 'drafts' }, 'failed', 'The push to GitHub failed.')] });
  await user.click(await screen.findByRole('button', { name: 'New Folder' }));
  await user.type(screen.getByRole('textbox', { name: 'Name of the new Folder' }), 'Drafts{Enter}');
  expect(await screen.findByRole('alert')).toHaveTextContent('Create Folder failed: The push to GitHub failed.');
  await user.click(screen.getByRole('button', { name: 'Dismiss' }));
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('refuses drops on Folders that a pending Folder job changes', async () => {
  const { requests } = renderTree({ jobs: [folderJob(7, 'folder.create', { folder: 'drafts' }), folderJob(8, 'folder.rename', { folder: 'work', new_folder: 'projects' })] });
  const drafts = (await screen.findByText('drafts')).closest('.folder-row')!;
  const rows = [drafts, ...['work', 'clients'].map(name => screen.getByRole('link', { name }).closest('.folder-row')!)];
  for (const row of rows) {
    fireEvent.dragOver(row, { dataTransfer: dragData({ slug: 'paper', folder: '' }) });
    expect(row).not.toHaveClass('is-drop-target');
    fireEvent.drop(row, { dataTransfer: dragData({ slug: 'paper', folder: '' }) });
  }
  const archive = screen.getByRole('link', { name: 'archive' }).closest('.folder-row')!;
  fireEvent.dragOver(archive, { dataTransfer: dragData({ slug: 'paper', folder: '' }) });
  expect(archive).toHaveClass('is-drop-target');
  expect(sent(requests, 'POST')).toHaveLength(0);
});

it('sends the Brain home to the parent Folder when the selected Folder is deleted', async () => {
  const user = userEvent.setup();
  renderTree({ route: `/brains/${brain.id}?folder=archive%2F2025`, after: () => [folderJob(20, 'folder.delete', { folder: 'archive/2025' }, 'succeeded')] });
  await user.click(await screen.findByRole('button', { name: 'Actions for 2025' }));
  await user.click(screen.getByRole('menuitem', { name: 'Delete' }));
  await user.click(within(await screen.findByRole('dialog', { name: 'Delete the Folder 2025?' })).getByRole('button', { name: 'Delete' }));
  await waitFor(() => expect(screen.getByTestId('search')).toHaveTextContent('?folder=archive'));
  expect(screen.getByTestId('search').textContent).toBe('?folder=archive');
});

it('reports a failed drop also when a later drop was made before it ended', async () => {
  const first = later();
  const { requests } = renderTree({ handle: (_request, path) => path.endsWith('/sources/paper/move') ? first.response : undefined });
  const archive = (await screen.findByRole('link', { name: 'archive' })).closest('.folder-row')!;
  fireEvent.drop(archive, { dataTransfer: dragData({ slug: 'paper', folder: 'work' }) });
  await waitFor(() => expect(sent(requests, 'POST')).toHaveLength(1));
  fireEvent.drop(screen.getByRole('link', { name: 'clients' }).closest('.folder-row')!, { dataTransfer: dragData({ slug: 'notes', folder: 'work', title: 'Notes' }) });
  expect(await screen.findByRole('status')).toHaveTextContent('Moving Notes to work/clients.');
  first.answer(json({ code: 'conflict', message: 'The Folder archive is being renamed or deleted.' }, 409));
  expect(await screen.findByRole('alert')).toHaveTextContent('Moving Paper failed: The Folder archive is being renamed or deleted.');
});

it('keeps a later edit open when an earlier request ends', async () => {
  const user = userEvent.setup();
  const create = later();
  renderTree({ handle: (request, path) => path === '/api/brains/brain-1/folders' && request.method === 'POST' ? create.response : undefined });
  await user.click(await screen.findByRole('button', { name: 'New Folder' }));
  await user.type(screen.getByRole('textbox', { name: 'Name of the new Folder' }), 'Drafts{Enter}');
  await user.click(screen.getByRole('button', { name: 'Actions for archive' }));
  await user.click(screen.getByRole('menuitem', { name: 'Rename' }));
  const input = screen.getByRole('textbox', { name: 'New name for archive' });
  await user.clear(input);
  await user.type(input, 'Old Archive');
  create.answer(json(folderJob(20, 'folder.create', { folder: 'drafts' }), 202));
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Creating the Folder drafts.'));
  expect(input).toBeInTheDocument();
  expect(input).toHaveValue('Old Archive');
  expect(input).toHaveFocus();
});

it('tracks a rename whose request ends after another rename started', async () => {
  const user = userEvent.setup();
  const first = later();
  renderTree({
    jobs: [folderJob(20, 'folder.rename', { folder: 'work', new_folder: 'projects' }, 'failed', 'The push to GitHub failed.')],
    handle: (_request, path) => path.endsWith('/folders/rename') ? first.response : undefined,
  });
  await user.click(await screen.findByRole('button', { name: 'Actions for work' }));
  await user.click(screen.getByRole('menuitem', { name: 'Rename' }));
  const input = screen.getByRole('textbox', { name: 'New name for work' });
  await user.clear(input);
  await user.type(input, 'Projects{Enter}');
  await user.click(screen.getByRole('button', { name: 'Cancel' }));
  await user.click(screen.getByRole('button', { name: 'Actions for archive' }));
  await user.click(screen.getByRole('menuitem', { name: 'Rename' }));
  first.answer(json(folderJob(20, 'folder.rename', { folder: 'work', new_folder: 'projects' }), 202));
  expect(await screen.findByRole('alert')).toHaveTextContent('Rename Folder failed: The push to GitHub failed.');
  expect(screen.getByRole('textbox', { name: 'New name for archive' })).toHaveFocus();
});

const pushed: Schema['FolderNode'] = { name: '', path: '', source_count: 0, total_source_count: 2, children: [
  { name: 'Field Notes', path: 'Field Notes', source_count: 2, total_source_count: 2, children: [
    { name: 'sub', path: 'Field Notes/sub', source_count: 0, total_source_count: 0, children: [] },
  ] },
] };

it('keeps Sources and new Folders out of a Folder whose name is not a slug', async () => {
  const user = userEvent.setup();
  const { requests } = renderTree({ folders: pushed });
  await user.click(await screen.findByRole('button', { name: 'Actions for sub' }));
  const notSlug = 'Rename Field Notes first. New Sources and Folders can go only into Folders whose names use a-z, 0-9, and hyphens.';
  for (const name of ['Rename', 'New Folder inside']) {
    expect(screen.getByRole('menuitem', { name })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('menuitem', { name })).toHaveAccessibleDescription(notSlug);
  }
  await user.keyboard('{Escape}');
  await user.click(screen.getByRole('button', { name: 'Actions for Field Notes' }));
  expect(screen.getByRole('menuitem', { name: 'New Folder inside' })).toHaveAttribute('aria-disabled', 'true');
  await user.click(screen.getByRole('menuitem', { name: 'Rename' }));
  const input = screen.getByRole('textbox', { name: 'New name for Field Notes' });
  expect(screen.getByText('The Folder has this name already.')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Rename' })).toBeDisabled();
  await user.keyboard('{Enter}');
  expect(sent(requests, 'POST')).toHaveLength(0);
  await user.clear(input);
  await user.type(input, 'Field notes');
  expect(screen.getByText('Will be saved as sources/field-notes/')).toBeInTheDocument();
  await user.keyboard('{Escape}');
  for (const name of ['Field Notes', 'sub']) {
    const row = screen.getByRole('link', { name }).closest('.folder-row')!;
    fireEvent.dragOver(row, { dataTransfer: dragData({ slug: 'paper', folder: '' }) });
    expect(row).not.toHaveClass('is-drop-target');
    fireEvent.drop(row, { dataTransfer: dragData({ slug: 'paper', folder: '' }) });
  }
  expect(sent(requests, 'POST')).toHaveLength(0);
});
