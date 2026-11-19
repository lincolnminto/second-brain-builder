import { afterEach, expect, it, vi } from 'vitest';
import { act, screen } from '@testing-library/react';
import { useJobEvents } from './events';
import { event } from '../../test/fixtures';
import { mockApi, renderApp } from '../../test/helpers';

class MockEventSource {
  static instances: MockEventSource[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  close = vi.fn();
  constructor(public url: string) { MockEventSource.instances.push(this); }
}
function Feed({ brainId }: { brainId: string }) {
  const feed = useJobEvents(brainId);
  return <div>{feed.connected ? 'connected' : 'disconnected'}<span>{feed.events.map(item => item.id).join(',')}</span></div>;
}
afterEach(() => vi.unstubAllGlobals());
it('deduplicates SSE events, invalidates Brain data, and closes the stream on unmount', () => {
  mockApi(); MockEventSource.instances = []; vi.stubGlobal('EventSource', MockEventSource);
  const { client, unmount } = renderApp(<Feed brainId="brain-1" />);
  client.setQueryData(['graph', 'brain-1'], { nodes: [], edges: [] });
  const stream = MockEventSource.instances[0]!;
  expect(stream.url).toBe('/api/events?brain_id=brain-1');
  act(() => stream.onopen?.());
  expect(screen.getByText('connected')).toBeInTheDocument();
  act(() => {
    stream.onmessage?.({ data: '{invalid' });
    stream.onmessage?.({ data: JSON.stringify(event) });
    stream.onmessage?.({ data: JSON.stringify(event) });
  });
  expect(screen.getByText('10')).toBeInTheDocument();
  expect(client.getQueryState(['graph', 'brain-1'])?.isInvalidated).toBe(true);
  act(() => stream.onerror?.());
  expect(screen.getByText('disconnected')).toBeInTheDocument();
  unmount(); expect(stream.close).toHaveBeenCalledOnce();
});
