import type { ForceAtlas2Settings } from 'graphology-layout-forceatlas2';

/** Floats per node and per edge in the ForceAtlas2 matrices (see graphology-layout-forceatlas2). */
export const NODE_STRIDE = 10;
export const EDGE_STRIDE = 3;
export type LayoutSettings = Required<ForceAtlas2Settings>;

export type LayoutRequest =
  | { type: 'load'; generation: number; nodes: ArrayBuffer; edges: ArrayBuffer; settings: LayoutSettings }
  /** `moved` holds `index, x, y` triples: pinned nodes that the user dragged. */
  | { type: 'step'; generation: number; iterations: number; moved: number[] };
/** `xy` holds `x, y` pairs in the node order of the last load. */
export type LayoutResult = { type: 'positions'; generation: number; xy: ArrayBuffer };
