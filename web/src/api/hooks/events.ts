import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api, result, type Schema } from '../client';

export function isJobEvent(value: unknown): value is Schema['JobEventView'] {
  return typeof value === 'object' && value !== null &&
    'id' in value && typeof value.id === 'number' &&
    'job_id' in value && typeof value.job_id === 'number' &&
    'kind' in value && typeof value.kind === 'string' &&
    'brain_id' in value && (typeof value.brain_id === 'string' || value.brain_id === null) &&
    'created_at' in value && typeof value.created_at === 'string' &&
    'data' in value && typeof value.data === 'object' && value.data !== null;
}

// Mount once in the app shell. Consumers share the feed through context.
// Native EventSource preserves Last-Event-ID on automatic reconnects.
export function useJobEvents(brainId?: string) {
  const cache = useQueryClient();
  const [feed, setFeed] = useState<{ brainId?: string; events: Schema['JobEventView'][]; connected: boolean }>({ events: [], connected: false });
  useEffect(() => {
    const query = brainId ? `?brain_id=${encodeURIComponent(brainId)}` : '';
    const source = new EventSource(`/api/events${query}`);
    let disposed = false;
    let checkingAuth = false;
    const invalidate = () => {
      for (const key of ['jobs', 'job', 'brains', 'brain', 'sources', 'source', 'folders', 'pages', 'page', 'graph', 'search', 'lfs-usage']) {
        void cache.invalidateQueries({ queryKey: [key] });
      }
    };
    source.onopen = () => {
      if (disposed) return;
      setFeed(previous => ({ brainId, events: previous.brainId === brainId ? previous.events : [], connected: true }));
      invalidate(); // Recover changes missed while disconnected.
    };
    source.onerror = () => {
      if (disposed) return;
      setFeed(previous => ({ ...previous, connected: false }));
      if (!checkingAuth) {
        checkingAuth = true;
        void result(api.GET('/api/setup')).catch(() => undefined).finally(() => { checkingAuth = false; });
      }
    };
    source.onmessage = (message) => {
      if (disposed) return;
      try {
        const event: unknown = JSON.parse(message.data);
        if (!isJobEvent(event)) return;
        setFeed(previous => ({ brainId, connected: true, events: [event, ...(previous.brainId === brainId ? previous.events : []).filter(item => item.id !== event.id)].slice(0, 100) }));
        invalidate();
      } catch { /* Ignore malformed events; the next valid event still arrives. */ }
    };
    return () => { disposed = true; source.close(); };
  }, [brainId, cache]);
  return feed.brainId === brainId ? feed : { brainId, events: [], connected: false };
}
