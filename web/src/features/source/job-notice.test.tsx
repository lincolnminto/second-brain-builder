import { afterEach, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { event, job } from '../../test/fixtures';
import type { Schema } from '../../api/client';
import { JobNotice } from './job-notice';
import { liveJobStatus } from './jobs';

afterEach(() => vi.useRealTimers());

it('tracks status events for the submitted job and hides success after four seconds', () => {
  vi.useFakeTimers();
  const queued = { ...job, status: 'queued' as const };
  const statusEvent = (status: string, id = 11): Schema['JobEventView'] => ({ ...event, id, kind: 'status', data: { status } });
  const view = (events: Schema['JobEventView'][]) => <MemoryRouter>
    <JobNotice job={queued} status={liveJobStatus(queued, events)} root="/brains/brain-1" />
  </MemoryRouter>;
  const { rerender } = render(view([]));
  expect(screen.getByRole('status')).toHaveTextContent('Job #1 is queued.');
  rerender(view([statusEvent('running'), statusEvent('queued', 10)]));
  expect(screen.getByRole('status')).toHaveTextContent('Job #1 is running.');
  rerender(view([statusEvent('succeeded', 12), statusEvent('running')]));
  expect(screen.getByRole('status')).toHaveTextContent('Job #1 succeeded.');
  act(() => vi.advanceTimersByTime(3999));
  expect(screen.getByRole('status')).toBeInTheDocument();
  act(() => vi.advanceTimersByTime(1));
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
});

it('keeps failure visible and ignores other jobs and invalid statuses', () => {
  vi.useFakeTimers();
  const queued = { ...job, status: 'queued' as const };
  expect(liveJobStatus(queued, [
    { ...event, kind: 'status', data: { status: 'bad' } },
    { ...event, id: 20, job_id: 99, kind: 'status', data: { status: 'succeeded' } },
  ])).toBe('queued');
  const status = liveJobStatus(queued, [{ ...event, kind: 'status', data: { status: 'failed' } }]);
  render(<MemoryRouter><JobNotice job={queued} status={status} root="/brains/brain-1" /></MemoryRouter>);
  expect(screen.getByRole('status')).toHaveTextContent('Job #1 failed.');
  act(() => vi.advanceTimersByTime(10_000));
  expect(screen.getByRole('status')).toBeInTheDocument();
});
