// ForceAtlas2 off the main thread. The matrices stay here between steps, so
// the algorithm keeps its per-node speed state; the main thread only receives
// positions. Vite bundles this file as a same-origin module worker, which the
// app's Content Security Policy (script-src 'self') allows.
import iterate from 'graphology-layout-forceatlas2/iterate.js';
import { NODE_STRIDE, type LayoutRequest, type LayoutResult, type LayoutSettings } from './layout-protocol';

type WorkerScope = {
  onmessage: ((event: MessageEvent<LayoutRequest>) => void) | null;
  postMessage: (message: LayoutResult, transfer: Transferable[]) => void;
};
const scope = self as unknown as WorkerScope;

let nodes = new Float32Array(0);
let edges = new Float32Array(0);
let settings: LayoutSettings | null = null;
let generation = -1;

scope.onmessage = ({ data }) => {
  if (data.type === 'load') {
    nodes = new Float32Array(data.nodes);
    edges = new Float32Array(data.edges);
    settings = data.settings;
    generation = data.generation;
    return;
  }
  if (data.generation !== generation || !settings) return;
  for (let index = 0; index + 2 < data.moved.length; index += 3) {
    const offset = data.moved[index]! * NODE_STRIDE;
    nodes[offset] = data.moved[index + 1]!;
    nodes[offset + 1] = data.moved[index + 2]!;
  }
  const count = nodes.length / NODE_STRIDE;
  if (count) for (let iteration = 0; iteration < data.iterations; iteration++) iterate(settings, nodes, edges);
  const xy = new Float32Array(count * 2);
  for (let index = 0; index < count; index++) {
    xy[index * 2] = nodes[index * NODE_STRIDE]!;
    xy[index * 2 + 1] = nodes[index * NODE_STRIDE + 1]!;
  }
  scope.postMessage({ type: 'positions', generation, xy: xy.buffer }, [xy.buffer]);
};
