import { useEffect, useRef, useState } from 'react';
import type { Schema } from '../../api/client';
import { applyPageEvent, emptyGraph, mergeSnapshot } from './data';

type TimedEvent = { event: Schema['JobEventView']; receivedAt: number };

// Key the owning component by Brain id. Existing feed entries form the baseline:
// replaying that history could resurrect a page deleted while we were offline.
// `addPages: false` keeps live writes from adding pages outside a Folder view.
export function useLiveGraph(snapshot: Schema['GraphView'] | undefined, events: Schema['JobEventView'][], fetching: boolean, brainId: string, addPages = true) {
  const [graph, setGraph] = useState(emptyGraph);
  const controller = useRef({
    graph: emptyGraph(), snapshot: undefined as Schema['GraphView'] | undefined,
    lastId: Math.max(0, ...events.map(event => event.id)),
    fetching: false, fetchBoundary: 0, pending: [] as TimedEvent[],
  });
  useEffect(() => {
    const state = controller.current;
    if (fetching && !state.fetching) {
      state.fetchBoundary = state.lastId;
      state.pending = [];
    }
    state.fetching = fetching;
    const incoming = events.filter(event => event.brain_id === brainId && event.id > state.lastId).sort((a, b) => a.id - b.id);
    for (const event of incoming) {
      state.lastId = Math.max(state.lastId, event.id);
      if (event.kind === 'page_written' || event.kind === 'page_deleted') {
        state.pending.push({ event, receivedAt: performance.now() });
      }
    }
    let changed = false;
    if (snapshot && snapshot !== state.snapshot) {
      state.graph = mergeSnapshot(state.graph, snapshot);
      state.snapshot = snapshot;
      // Events received during an in-flight GET win over its older snapshot.
      for (const item of state.pending) {
        if (item.event.id > state.fetchBoundary) state.graph = applyPageEvent(state.graph, item.event, item.receivedAt, addPages);
      }
      changed = true;
    } else if (state.snapshot && incoming.length) {
      const receivedAt = performance.now();
      const before = state.graph;
      for (const event of incoming) state.graph = applyPageEvent(state.graph, event, receivedAt, addPages);
      changed = state.graph !== before;
    }
    if (changed) setGraph(state.graph);
  }, [snapshot, events, fetching, brainId, addPages]);
  return graph;
}
