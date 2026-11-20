// graphology-layout-forceatlas2 ships this module (one algorithm iteration
// over flat matrices) without types. Its signature is stable since 0.4.
declare module 'graphology-layout-forceatlas2/iterate.js' {
  import type { ForceAtlas2Settings } from 'graphology-layout-forceatlas2';
  export default function iterate(settings: Required<ForceAtlas2Settings>, nodes: Float32Array, edges: Float32Array): void;
}
