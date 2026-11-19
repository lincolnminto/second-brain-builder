import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { Route, Routes } from 'react-router';
import { ActivityRail } from '../../app/activity';
import { BrainContext } from '../../app/context';
import { brain, job, now, source } from '../../test/fixtures';
import { json, mockApi, renderApp } from '../../test/helpers';
import { SourcePlaceholder } from '../source';

afterEach(() => vi.unstubAllGlobals());

it.each(['activity', 'source'])('confirms Stop and shows Stopping until the job ends in %s', async location => {
  let current = { ...job, kind: 'source.ingest' as const, payload: { source_slug: source.slug }, status: 'running' as 'running' | 'cancelled', finished_at: null as string | null, stop_requested_at: null as string | null };
  const requests = mockApi((request, path) => {
    if (path === '/api/jobs') return json([current]);
    if (path.endsWith('/cancel') && request.method === 'POST') {
      current = { ...current, stop_requested_at: now };
      return json(current);
    }
    if (path.endsWith('/sources/paper')) return json({ source: { ...source, status: 'ingesting' }, citing_pages: [] });
    if (path.endsWith('/ingest-estimate')) return json({ parts_done: 12, parts_total: 128, estimated_tokens: 1000 });
    if (path.includes('/pages/')) return json({ body: 'Document.', sources: [], tags: [] });
  });
  const user = userEvent.setup();
  const { client } = renderApp(<BrainContext.Provider value={{ brainId: brain.id, brain, events: [], connected: true }}>
    {location === 'activity' ? <ActivityRail /> : <Routes><Route path="/brains/:brainId/sources/:slug" element={<SourcePlaceholder />} /></Routes>}
  </BrainContext.Provider>, '/brains/brain-1/sources/paper');
  await user.click(await screen.findByRole('button', { name: 'Stop' }));
  const dialog = await screen.findByRole('dialog', { name: 'Stop this Ingest?' });
  expect(dialog).toHaveTextContent('Work saved so far is kept.');
  expect(requests.some(request => request.method === 'POST')).toBe(false);
  await user.click(within(dialog).getByRole('button', { name: 'Stop' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(await screen.findByRole('button', { name: 'Stopping...' })).toBeDisabled();
  expect(requests.filter(request => request.url.endsWith('/cancel') && request.method === 'POST')).toHaveLength(1);
  current = { ...current, status: 'cancelled', finished_at: now };
  await act(async () => { await client.invalidateQueries({ queryKey: ['jobs'] }); });
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Stopping...' })).not.toBeInTheDocument());
  expect(screen.getByText('Cancelled', { selector: '.badge' })).toBeInTheDocument();
});

it('shows a persisted stop request after reopening Activity', async () => {
  mockApi((_request, path) => path === '/api/jobs' ? json([{ ...job, status: 'running', stop_requested_at: now }]) : undefined);
  renderApp(<BrainContext.Provider value={{ brainId: brain.id, brain, events: [], connected: true }}><ActivityRail /></BrainContext.Provider>);
  expect(await screen.findByRole('button', { name: 'Stopping...' })).toBeDisabled();
  expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument();
});
