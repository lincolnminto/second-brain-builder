import { afterEach, expect, it, vi } from 'vitest';
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrainContext } from '../../app/context';
import { brain } from '../../test/fixtures';
import { json, mockApi, renderApp } from '../../test/helpers';
import { WikiPlaceholder } from './index';

afterEach(() => vi.unstubAllGlobals());

const context = { brainId: brain.id, brain, events: [], connected: true };
const tree = { path: 'wiki/entities/tree.md', title: 'Tree', type: 'entity', description: null };
const heron = { path: 'wiki/entities/heron.md', title: 'Heron', type: 'entity', description: null };
const names = { path: 'wiki/name-indexes/book-a-z.md', title: 'Book: names a-z', type: 'name-index', description: null };
const canonical = { path: 'sources/nature/birds/book/book.md', title: 'Book', type: 'source', description: null };
const folders = { name: '', path: '', source_count: 0, total_source_count: 1, children: [
  { name: 'nature', path: 'nature', source_count: 0, total_source_count: 1, children: [{ name: 'birds', path: 'nature/birds', source_count: 1, total_source_count: 1, children: [] }] },
  { name: 'empty', path: 'empty', source_count: 0, total_source_count: 0, children: [] },
] };
const typeFilter = () => screen.getByRole('group', { name: 'Filter by page type' });

it('shows only the page types that have pages, and the empty ones disabled with a hint under All types', async () => {
  const user = userEvent.setup();
  mockApi((_request, path) => path.endsWith('/pages') ? json([tree, names, canonical]) : undefined);
  renderApp(<BrainContext.Provider value={context}><WikiPlaceholder /></BrainContext.Provider>);
  await screen.findByRole('region', { name: 'Name indexes' });
  const chips = () => within(typeFilter()).getAllByRole('button').map(button => button.textContent);
  expect(chips()).toEqual(['Entity 1', 'Name index 1', 'All types']);
  expect(screen.queryByRole('combobox', { name: 'Folder' })).not.toBeInTheDocument();

  await user.click(screen.getByRole('button', { name: 'All types' }));
  expect(screen.getByRole('button', { name: 'All types' })).toHaveAttribute('aria-pressed', 'true');
  expect(chips()).toEqual(['Entity 1', 'Name index 1', 'Summary 0', 'Concept 0', 'Comparison 0', 'Synthesis 0', 'All types']);
  const comparison = screen.getByRole('button', { name: 'Comparison 0' });
  expect(comparison).toHaveAttribute('aria-disabled', 'true');
  expect(comparison).toHaveAccessibleDescription('Ingest writes comparison pages when a Source compares things');
  await user.hover(comparison);
  expect(screen.getByRole('tooltip')).toHaveTextContent('Ingest writes comparison pages when a Source compares things');
  await user.click(comparison);
  expect(screen.getAllByRole('region').map(region => region.getAttribute('aria-label'))).toEqual(['Entity', 'Name indexes']);
  await user.unhover(comparison);
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  // Escape closes a tooltip that hover opened while the focus is elsewhere.
  act(() => comparison.blur());
  await user.hover(comparison);
  expect(screen.getByRole('tooltip')).toHaveTextContent('Ingest writes comparison pages when a Source compares things');
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  await user.unhover(comparison);
  act(() => screen.getByRole('button', { name: 'Synthesis 0' }).focus());
  await waitFor(() => expect(screen.getByRole('tooltip')).toHaveTextContent('Synthesis pages come from answers saved by you or your agents'));
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();

  await user.click(screen.getByRole('button', { name: 'Entity 1' }));
  expect(screen.getByRole('button', { name: 'Entity 1' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getAllByRole('region').map(region => region.getAttribute('aria-label'))).toEqual(['Entity']);
  await user.click(screen.getByRole('button', { name: 'Entity 1' }));
  expect(screen.getAllByRole('region')).toHaveLength(2);
});

it('filters the Wiki home by Folder with GET /pages?folder=', async () => {
  const user = userEvent.setup();
  const requests = mockApi((request, path) => {
    if (path.endsWith('/folders')) return json(folders);
    if (!path.endsWith('/pages')) return undefined;
    const folder = new URL(request.url).searchParams.get('folder');
    return json(folder === 'nature/birds' ? [heron, canonical] : folder === 'empty' ? [] : [tree, heron, names]);
  });
  renderApp(<BrainContext.Provider value={context}><WikiPlaceholder /></BrainContext.Provider>, `/brains/${brain.id}/wiki`);
  const select = await screen.findByRole('combobox', { name: 'Folder' });
  expect(within(select).getAllByRole('option').map(option => option.textContent)).toEqual(['All Folders', 'nature', 'nature/birds', 'empty']);
  expect(await screen.findByRole('link', { name: 'Tree' })).toBeInTheDocument();

  await user.selectOptions(select, 'nature/birds');
  await waitFor(() => expect(screen.queryByRole('link', { name: 'Tree' })).not.toBeInTheDocument());
  expect(screen.getByRole('link', { name: 'Heron' })).toBeInTheDocument();
  expect(requests.map(request => new URL(request.url)).filter(url => url.pathname.endsWith('/pages')).map(url => url.searchParams.get('folder'))).toEqual([null, 'nature/birds']);
  expect(within(typeFilter()).getAllByRole('button').map(button => button.textContent)).toEqual(['Entity 1', 'All types']);

  await user.selectOptions(select, 'empty');
  expect(await screen.findByRole('heading', { name: 'No Wiki Pages for this Folder yet' })).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Clear the Folder filter' }));
  expect(await screen.findByRole('link', { name: 'Tree' })).toBeInTheDocument();
  expect(select).toHaveValue('');
});
