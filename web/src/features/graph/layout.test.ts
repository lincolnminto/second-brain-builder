import { UndirectedGraph } from 'graphology';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LayoutController, layoutSettings } from './layout';
import { NODE_STRIDE, type LayoutRequest, type LayoutResult } from './layout-protocol';
import type { RenderEdge, RenderGraph, RenderNode } from './sync';

/** Stands in for the ForceAtlas2 worker: records requests, answers on demand. */
class FakeWorker {
  onmessage: ((event: MessageEvent<LayoutResult>) => void) | null = null;
  readonly requests: LayoutRequest[] = [];
  postMessage(message: LayoutRequest) { this.requests.push(message); }
  terminate() {}
  loads() { return this.requests.filter(request => request.type === 'load'); }
  /** Answer the last step with these `x, y` pairs. */
  answer(xy: number[], generation = this.requests.at(-1)!.generation) {
    this.onmessage!({ data: { type: 'positions', generation, xy: new Float32Array(xy).buffer } } as MessageEvent<LayoutResult>);
  }
}

let clock = 0;
beforeEach(() => {
  clock = 0;
  vi.spyOn(performance, 'now').mockImplementation(() => clock);
  // Animated steps wait for the next frame; the tests answer steps themselves.
  vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1));
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
  return () => vi.unstubAllGlobals();
});

function setup(animate: boolean, pinned: string[] = []) {
  const graph: RenderGraph = new UndirectedGraph<RenderNode, RenderEdge>();
  ['a', 'b', 'c'].forEach((key, index) => graph.addNode(key, {
    x: index, y: 0, size: 4, label: key, type: 'concept', degree: 1, hub: false, fixed: pinned.includes(key),
  }));
  graph.addEdge('a', 'b', { curvature: 0 });
  graph.addEdge('b', 'c', { curvature: 0 });
  const worker = new FakeWorker();
  const onSettled = vi.fn();
  const layout = new LayoutController(graph, { animate, onSettled, createWorker: () => worker as unknown as Worker });
  const xs = () => graph.mapNodes((_key, { x }) => x);
  const loadedXs = (load: LayoutRequest) => {
    if (load.type !== 'load') throw new Error('Not a load');
    const nodes = new Float32Array(load.nodes);
    return [0, 1, 2].map(index => nodes[index * NODE_STRIDE]);
  };
  return { graph, worker, layout, onSettled, xs, loadedXs };
}

describe('layout controller', () => {
  it('with reduced motion, runs until the layout settles and then moves the pages once', () => {
    const { worker, layout, onSettled, xs } = setup(false);
    layout.restart(false);
    worker.answer([10, 0, 20, 0, 30, 0]);
    expect(xs()).toEqual([0, 1, 2]);
    clock = 600;
    worker.answer([10, 0, 20, 0, 30, 0]);
    expect(layout.isRunning).toBe(true);
    worker.answer([10, 0, 20, 0, 30, 0]);
    expect(layout.isRunning).toBe(false);
    expect(xs()).toEqual([10, 20, 30]);
    expect(onSettled).toHaveBeenCalledTimes(1);
  });

  it('keeps the progress of an interrupted run, and a live update does not cut a full run short', () => {
    const { worker, layout, xs, loadedXs } = setup(false);
    layout.restart(false);
    worker.answer([10, 0, 20, 0, 30, 0]);
    // A page_written event changes the structure while the first run is in flight.
    layout.restart(true);
    expect(xs()).toEqual([10, 20, 30]);
    const [first, second] = worker.loads();
    expect(loadedXs(second!)).toEqual([10, 20, 30]);
    expect(second!.type === 'load' && second!.settings.slowDown).toBe(layoutSettings(3, false).slowDown);
    // A late answer to the old run does not apply.
    worker.answer([99, 0, 99, 0, 99, 0], first!.generation);
    clock = 600;
    worker.answer([11, 0, 21, 0, 31, 0]);
    worker.answer([11, 0, 21, 0, 31, 0]);
    worker.answer([11, 0, 21, 0, 31, 0]);
    expect(layout.isRunning).toBe(false);
    expect(xs()).toEqual([11, 21, 31]);

    // Once a layout has settled, a change starts a slower, incremental run.
    layout.restart(true);
    const third = worker.loads()[2]!;
    expect(third.type === 'load' && third.settings.slowDown).toBe(layoutSettings(3, true).slowDown);
  });

  it('never moves a pinned page, sends its position with each step, and stops at the time budget', () => {
    const { worker, layout, onSettled, xs } = setup(true, ['a']);
    layout.restart(true);
    expect(worker.requests.at(-1)).toMatchObject({ type: 'step', moved: [0, 0, 0] });
    worker.answer([50, 0, 60, 0, 70, 0]);
    expect(xs()).toEqual([0, 60, 70]);
    clock = 1700;
    worker.answer([50, 0, 65, 0, 75, 0]);
    expect(layout.isRunning).toBe(false);
    expect(xs()).toEqual([0, 65, 75]);
    expect(onSettled).toHaveBeenCalledTimes(1);
  });
});
