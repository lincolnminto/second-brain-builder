import { afterEach, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
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

function renderSource(drops: { notes_dropped_total: number; parts_with_drops: number } = { notes_dropped_total: 0, parts_with_drops: 0 }) {
  const requests = mockApi((request, path) => {
    if (path === '/api/jobs') return json([]);
    if (path.endsWith('/ingest-estimate')) return json({ parts_total: 1, parts_done: 1, estimated_tokens: 0 });
    if (path.endsWith('/sources/paper') && request.method === 'GET') return json({ source: { ...source, ...drops }, citing_pages: [{ title: 'Attention', type: 'concept', path: 'wiki/concepts/attention.md' }] });
    if (path.includes('/pages/')) return json({ path: source.canonical_path, title: 'Paper', type: 'source', body: 'Faithful **Canonical Document**\n\n<script>bad()</script>', sources: [], tags: [], description: null, generated_by: null, generated_at: null });
    if (request.method === 'DELETE') return json({ ...job, id: 3, kind: 'source.delete', status: 'queued' }, 202);
    if (path.endsWith('/move')) return json({ ...job, id: 4, kind: 'source.move', status: 'queued' }, 202);
  });
  renderApp(<BrainContext.Provider value={{ brainId: brain.id, brain, events: [], connected: true }}><Routes><Route path="/brains/:brainId/sources/:slug" element={<SourcePlaceholder />} /></Routes></BrainContext.Provider>, '/brains/brain-1/sources/paper');
  return requests;
}

it.each([0, 7])('shows note drops only when the total is positive: %i', async count => {
  renderSource({ notes_dropped_total: count, parts_with_drops: count > 0 ? 2 : 0 });
  await screen.findByRole('link', { name: 'Download original' });
  if (count > 0) expect(screen.getByText('7 notes dropped in 2 Parts')).toBeInTheDocument();
  else expect(screen.queryByText(/notes dropped in/)).not.toBeInTheDocument();
});

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

it('continues partly ingested Sources with resume and a chosen token limit', async () => {
  const partly = { ...source, status: 'partly_ingested' as const, parts_done: 12, parts_total: 131 };
  const requests = mockApi((request, path) => {
    if (path === '/api/jobs') return json([]);
    if (path.endsWith('/ingest-estimate')) return json({ parts_total: 131, parts_done: 12, estimated_tokens: 4_100_000 });
    if (path.endsWith('/sources/paper') && request.method === 'GET') return json({ source: partly, citing_pages: [] });
    if (path.includes('/pages/')) return json({ path: partly.canonical_path, title: 'Paper', type: 'source', body: 'Done.', sources: [], tags: [], description: null, generated_by: null, generated_at: null });
    if (path.endsWith('/ingest') && request.method === 'POST') return json({ ...job, id: 9, kind: 'source.ingest', status: 'queued' }, 202);
  });
  const user = userEvent.setup();
  renderApp(<BrainContext.Provider value={{ brainId: brain.id, brain, events: [], connected: true }}><Routes><Route path="/brains/:brainId/sources/:slug" element={<SourcePlaceholder />} /></Routes></BrainContext.Provider>, '/brains/brain-1/sources/paper');
  await screen.findByText('Partly ingested · 12 of 131 Parts');
  await user.click(screen.getByRole('button', { name: 'Continue Ingest' }));
  const dialog = await screen.findByRole('dialog', { name: 'Continue Ingest' });
  expect(dialog).toHaveTextContent('119 Parts remain');
  expect(dialog).toHaveTextContent('About 4.1 M tokens');
  await user.clear(within(dialog).getByLabelText('Run token limit'));
  await user.type(within(dialog).getByLabelText('Run token limit'), '250000');
  await user.click(within(dialog).getByRole('button', { name: 'Continue' }));
  expect(await screen.findByRole('status')).toHaveTextContent('Job #9 is queued');
  const ingest = requests.find(request => request.method === 'POST' && request.url.includes('/ingest'));
  expect(await ingest?.json()).toEqual({ resume: true, max_tokens: 250_000, mode: 'auto' });
});

it.each([
  { status: 'ingesting', phase: 'map', label: 'Extracting Parts 40 of 128' },
  { status: 'partly_ingested', phase: 'reduce', label: 'Writing pages 120 of 450' },
])('shows map-reduce progress and both estimate phases: $phase', async ({ status, phase, label }) => {
  mockApi((_request, path) => {
    if (path === '/api/jobs') return json([]);
    if (path.endsWith('/sources/paper')) return json({ source: { ...source, mode: 'map_reduce', status, phase, parts_total: 128, map_parts_done: 40, reduce_groups_done: 120, reduce_groups_total: 450, reduce_groups_major: 3, name_index_entries: 1422, name_index_pages: 12 }, citing_pages: [] });
    if (path.endsWith('/ingest-estimate')) return json({ mode: 'map_reduce', parts_total: 128, parts_done: 40, estimated_tokens: 3000, map_estimated_tokens: 1000, reduce_estimated_tokens: 2000 });
  });
  renderApp(<BrainContext.Provider value={{ brainId: brain.id, brain, events: [], connected: true }}><Routes><Route path="/brains/:brainId/sources/:slug" element={<SourcePlaceholder />} /></Routes></BrainContext.Provider>, '/brains/brain-1/sources/paper');
  expect(await screen.findByText(label)).toBeInTheDocument();
  expect(screen.getByText('3 important rare names · 1422 names in 12 Name indexes')).toBeInTheDocument();
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: status === 'partly_ingested' ? 'Continue Ingest' : 'Ingest Source' }));
  const dialog = await screen.findByRole('dialog');
  expect(dialog).toHaveTextContent('Extracting Parts: About 1.0 K tokens');
  expect(dialog).toHaveTextContent('Writing pages: About 2.0 K tokens');
  expect(dialog).toHaveTextContent('The run token limit covers both phases.');
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

it.each([
  { parts: 11, estimateParts: 5, tokens: 1000, dialog: true, status: 'converted' as const },
  { parts: null, estimateParts: 11, tokens: 1000, dialog: true, status: 'converted' as const },
  { parts: 5, estimateParts: 5, tokens: 400_001, dialog: true, status: 'converted' as const },
  { parts: 10, estimateParts: 10, tokens: 400_000, dialog: false, status: 'converted' as const },
  { parts: 11, estimateParts: 11, tokens: 1_056_000, dialog: true, status: 'ingested' as const },
])('checks the estimate before starting a new Ingest: %j', async ({ parts, estimateParts, tokens, dialog: needsDialog, status }) => {
  const converted = { ...source, status, parts_done: status === 'ingested' ? parts : parts == null ? null : 0, parts_total: parts };
  const requests = mockApi((request, path) => {
    if (path === '/api/jobs') return json([]);
    if (path.endsWith('/ingest-estimate')) return json({ parts_total: estimateParts, parts_done: 0, estimated_tokens: tokens });
    if (path.endsWith('/sources/paper')) return json({ source: converted, citing_pages: [] });
    if (path.includes('/pages/')) return json({ path: source.canonical_path, title: 'Paper', type: 'source', body: 'Document.', sources: [], tags: [], description: null, generated_by: null, generated_at: null });
    if (path.endsWith('/ingest') && request.method === 'POST') return json({ ...job, id: 9, kind: 'source.ingest', status: 'queued' }, 202);
  });
  const user = userEvent.setup();
  renderApp(<BrainContext.Provider value={{ brainId: brain.id, brain, events: [], connected: true }}><Routes><Route path="/brains/:brainId/sources/:slug" element={<SourcePlaceholder />} /></Routes></BrainContext.Provider>, '/brains/brain-1/sources/paper');
  const start = await screen.findByRole('button', { name: 'Ingest Source' });
  await waitFor(() => expect(start).toBeEnabled());
  await user.click(start);
  if (needsDialog) {
    const dialog = await screen.findByRole('dialog', { name: 'Ingest Source' });
    expect(dialog).toHaveTextContent(`${estimateParts} Parts remain`);
    expect(requests.some(request => request.method === 'POST')).toBe(false);
    await user.clear(within(dialog).getByLabelText('Run token limit'));
    await user.type(within(dialog).getByLabelText('Run token limit'), '250000');
    await user.click(within(dialog).getByRole('button', { name: 'Ingest Source' }));
  } else {
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  }
  expect(await screen.findByRole('status')).toHaveTextContent('Job #9 is queued');
  const estimateRequest = requests.find(request => request.url.includes('/ingest-estimate'))!;
  expect(new URL(estimateRequest.url).searchParams.get('resume')).toBe(String(status !== 'ingested'));
  expect(await requests.find(request => request.method === 'POST')?.json()).toEqual({ resume: status !== 'ingested', max_tokens: needsDialog ? 250_000 : null, mode: 'auto' });
});
