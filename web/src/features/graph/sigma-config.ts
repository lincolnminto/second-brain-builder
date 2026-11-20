// Sigma programs and settings. This module touches WebGL constants, so only
// the lazily loaded canvas imports it.
import { layerGradient, pathCurved, sdfCircle, type FragmentLayer } from 'sigma/rendering';
import type { Settings } from 'sigma/settings';
import { HALO_CORE } from './style';
import { strings } from './strings';

const float = (value: number) => value.toFixed(4);

/** One node layer: a glow outside the disc, a ring in the node color, and a
 * softer core. Node quads are larger than the disc (see `HALO_CORE`), so the
 * glow has room; the layer returns straight alpha, as sigma's layers do. */
function haloLayer(): FragmentLayer {
  const { FLOAT, UNSIGNED_BYTE } = WebGL2RenderingContext;
  return {
    name: 'halo',
    uniforms: [],
    attributes: [
      { name: 'haloRing', size: 4, type: UNSIGNED_BYTE, normalized: true, source: 'color' },
      { name: 'haloCore', size: 4, type: UNSIGNED_BYTE, normalized: true, source: 'coreColor' },
      { name: 'haloGlow', size: 1, type: FLOAT, source: 'glow' },
    ],
    glsl: /* glsl */ `
vec4 layer_halo(vec4 ring, vec4 core, float glow) {
  float r = length(context.uv);
  float aa = context.aaWidth;
  float outer = ${float(HALO_CORE)};
  float inner = outer - clamp(2.0 * context.pixelToUV, ${float(HALO_CORE * 0.12)}, ${float(HALO_CORE * 0.4)});
  float falloff = 1.0 - smoothstep(outer, 1.0, r);
  vec4 color = vec4(ring.rgb, ring.a * glow * falloff * falloff);
  color = blendOver(color, vec4(ring.rgb, ring.a * smoothstep(outer + aa, outer - aa, r)));
  return blendOver(color, vec4(core.rgb, core.a * smoothstep(inner + aa, inner - aa, r)));
}
`,
  };
}

export function graphPrimitives() {
  return {
    nodes: {
      shapes: [sdfCircle()],
      layers: [haloLayer()],
      variables: {
        coreColor: { type: 'color' as const, default: '#888888' },
        glow: { type: 'number' as const, default: 0.3 },
      },
      label: { font: { family: 'Geist, sans-serif', weight: '500', size: 12 }, margin: 4 },
    },
    edges: {
      paths: [pathCurved()],
      layers: [layerGradient({ stops: [{ node: 'source' }, { node: 'target' }] })],
      defaultHead: 'none',
      defaultTail: 'none',
    },
  };
}

export function graphSettings(compact: boolean, reducedMotion: boolean): Partial<Settings> {
  const duration = reducedMotion ? 0 : 250;
  return {
    itemSizesReference: 'screen',
    autoRescale: true,
    stagePadding: compact ? 24 : 56,
    minCameraRatio: 0.03,
    maxCameraRatio: 4,
    enableNodeDrag: !compact,
    enableCameraRotation: false,
    enableCameraMouseRotation: false,
    gestureTarget: compact ? 'shared' : 'graph',
    sharedGestureWheelMessage: strings.sharedWheel,
    sharedGestureAppleWheelMessage: strings.sharedWheelApple,
    sharedGestureTouchMessage: strings.sharedTouch,
    labelDensity: compact ? 0.45 : 0.8,
    labelGridCellSize: 140,
    labelRenderedSizeThreshold: compact ? 12 : 9,
    minEdgeThickness: 0.6,
    nodePickingPadding: 2,
    zoomDuration: duration,
    inertiaDuration: reducedMotion ? 0 : 200,
    doubleClickZoomingDuration: duration,
    allowInvalidContainer: true,
  };
}

/** Bindings that sigma evaluates before the reducers. Node drag needs the
 * `x` and `y` attribute bindings. */
export const graphStyles = {
  nodes: {
    x: { attribute: 'x' },
    y: { attribute: 'y' },
    size: { attribute: 'size', defaultValue: 8 },
    label: { attribute: 'label' },
    labelFont: '500 Geist, sans-serif',
    labelSize: 12,
    labelPosition: 'right',
    depth: 'nodes',
    cursor: 'pointer',
    backdropArea: 'label',
    backdropCornerRadius: 11,
    backdropLabelPadding: 5,
    backdropPadding: 0,
    backdropBorderWidth: 1,
    backdropShadowBlur: 10,
  },
  edges: {
    path: 'curved',
    size: 1,
    depth: 'edges',
  },
} as const;
