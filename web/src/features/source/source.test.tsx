import { afterEach, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router';
import { BrainContext } from '../../app/context';
import { brain, job, source, event } from '../../test/fixtures';
import { json, mockApi, renderApp } from '../../test/helpers';
import { SourcePlaceholder } from './index';
import { sourceJobs } from './jobs';

afterEach(() => vi.unstubAllGlobals());

it('associates Source jobs using payloads, file hashes, and live Conversion events within the same Brain', () => {
  const pending = { ...job, id: 2, kind: 'source.add' as const, payload: { sha256: source.sha256 } };
  const ingest = { ...job, id: 3, kind: 'source.ingest' as const, payload: { source_slug: source.slug } };
  const converting = { ...job, id: 4, kind: 'source.add' as const, payload: {} };
  const unrelated = { ...job, id: 5, kind: 'source.add' as const, brain_id: 'other', payload: { sha256: source.sha256 } };
  expect(sourceJobs(source, [pending, ingest, converting, unrelated, job], [{ ...event, job_id: 4, kind: 'progress', data: { source: source.slug } }]).map(item => item.id)).toEqual([4, 3, 2]);
});

function renderSource() {
  const requests = mockApi((request, path) => {
    if (path === '/api/jobs') return json([]);
    if (path.endsWith('/sources/paper') && request.method === 'GET') return json({ source, citing_pages: [{ title: 'Attention', type: 'concept', path: 'wiki/concepts/attention.md' }] });
    if (path.includes('/pages/')) return json({ path: source.canonical_path, title: 'Paper', type: 'source', body: 'Faithful **Canonical Document**\n\n<script>bad()</script>', sources: [], tags: [], description: null, generated_by: null, generated_at: null });
    if (request.method === 'DELETE') return json({ ...job, id: 3, kind: 'source.delete', status: 'queued' }, 202);
    if (path.endsWith('/move')) return json({ ...job, id: 4, kind: 'source.move', status: 'queued' }, 202);
  });
  renderApp(<BrainContext.Provider value={{ brainId: brain.id, brain, events: [], connected: true }}><Routes><Route path="/brains/:brainId/sources/:slug" element={<SourcePlaceholder />} /></Routes></BrainContext.Provider>, '/brains/brain-1/sources/paper');
  return requests;
}

it('renders the Canonical Document and citing Wiki Pages and confirms deletion with its history warning', async () => {
  const user = userEvent.setup(); const requests = renderSource();
  expect(await screen.findByRole('link', { name: 'Download original' })).toHaveAttribute('href', '/api/brains/brain-1/sources/paper/original');
  expect(await screen.findByText('Canonical Document', { selector: 'strong' })).toBeInTheDocument();
  expect(document.querySelector('script')).toBeNull();
  expect(screen.getByRole('link', { name: 'Attention' })).toHaveAttribute('href', '/brains/brain-1/wiki/wiki/concepts/attention.md');
  await user.click(screen.getByRole('button', { name: 'Delete Source' }));
  const dialog = await screen.findByRole('dialog', { name: 'Delete this Source?' });
  expect(dialog).toHaveTextContent('Git history keeps the file.');
  expect(requests.filter(request => request.method === 'DELETE')).toHaveLength(0);
  await user.click(within(dialog).getByRole('button', { name: 'Delete Source' }));
  expect(await screen.findByRole('status')).toHaveTextContent('Deletion is queued as job #3');
  expect(requests.find(request => request.method === 'DELETE')?.headers.get('X-SBB-Client')).toBe('web');
});

it('moves the Source using the chosen Folder path and reports the queued job', async () => {
  const user = userEvent.setup(); const requests = renderSource();
  await user.click(await screen.findByRole('button', { name: 'Move Source' }));
  const dialog = await screen.findByRole('dialog', { name: 'Move this Source' });
  await user.type(within(dialog).getByLabelText('Folder path'), 'Research/Books');
  await user.click(within(dialog).getByRole('button', { name: 'Move Source' }));
  expect(await screen.findByRole('status')).toHaveTextContent('Job #4 is queued.');
  expect(await requests.find(request => new URL(request.url).pathname.endsWith('/move'))?.json()).toEqual({ folder: 'Research/Books' });
});
