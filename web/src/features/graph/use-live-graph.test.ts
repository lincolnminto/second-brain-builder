import { renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { Schema } from '../../api/client';
import { useLiveGraph } from './use-live-graph';

const page = { path: 'wiki/concepts/a.md', title: 'Attention', type: 'concept' };
const snapshot: Schema['GraphView'] = { nodes: [page], edges: [] };
const empty: Schema['GraphView'] = { nodes: [], edges: [] };
function written(id: number): Schema['JobEventView'] {
  return { id, job_id: 1, brain_id: 'brain-1', created_at: '2026-11-19T12:00:00Z', kind: 'page_written', data: { ...page, links: [] } };
}
type Props = { data?: Schema['GraphView']; events: Schema['JobEventView'][]; fetching: boolean };
function render(initialProps: Props) {
  return renderHook(({ data, events, fetching }: Props) => useLiveGraph(data, events, fetching, 'brain-1'), { initialProps });
}

describe('live graph reconciliation', () => {
  it('uses the initial snapshot without replaying historical cached events', () => {
    const { result } = render({ data: empty, events: [written(1)], fetching: false });
    expect(result.current.nodes).toEqual([]);
  });

  it('retains writes received before the initial GET completes', () => {
    const { result, rerender } = render({ events: [], fetching: true });
    rerender({ events: [written(1)], fetching: true });
    expect(result.current.nodes).toEqual([]);
    rerender({ data: empty, events: [written(1)], fetching: false });
    expect(result.current.nodes[0]).toMatchObject(page);
  });

  it('deduplicates event ids, applies newest-first feeds chronologically, and ignores other Brains', () => {
    const { result, rerender } = render({ data: empty, events: [], fetching: false });
    const events = [{ ...written(3), kind: 'page_deleted' }, written(2), written(1)];
    rerender({ data: empty, events, fetching: false });
    expect(result.current.nodes).toEqual([]);
    rerender({ data: empty, events: [written(2), written(1)], fetching: false });
    expect(result.current.nodes).toEqual([]);
    rerender({ data: empty, events: [{ ...written(4), brain_id: 'another-brain' }], fetching: false });
    expect(result.current.nodes).toEqual([]);
  });

  it('lets a page_deleted event win over an in-flight stale GET without losing surviving node coordinates', () => {
    const other = { ...page, path: 'wiki/concepts/b.md', title: 'Birds' };
    const initial = { nodes: [page, other], edges: [{ from_path: page.path, to_path: other.path }] };
    const { result, rerender } = render({ data: initial, events: [], fetching: false });
    const survivor = result.current.nodes[1]!;
    Object.assign(survivor, { x: 50, y: 60 });
    rerender({ data: initial, events: [], fetching: true });
    const deleted = { ...written(1), kind: 'page_deleted' };
    rerender({ data: initial, events: [deleted], fetching: true });
    rerender({ data: structuredClone(initial), events: [deleted], fetching: false });
    expect(result.current.nodes).toEqual([survivor]);
    expect(result.current.nodes[0]).toMatchObject({ x: 50, y: 60 });
    expect(result.current.links).toEqual([]);
  });

  it('accepts a later authoritative reconnect snapshot without resurrecting previously processed pages', () => {
    const { result, rerender } = render({ data: empty, events: [], fetching: false });
    rerender({ data: empty, events: [written(1)], fetching: true });
    rerender({ data: snapshot, events: [written(1)], fetching: false });
    expect(result.current.nodes).toHaveLength(1);
    // A deletion happened while disconnected, outside the retained SSE feed.
    rerender({ data: snapshot, events: [written(1)], fetching: true });
    rerender({ data: { nodes: [], edges: [] }, events: [written(1)], fetching: false });
    expect(result.current.nodes).toEqual([]);
  });
});
