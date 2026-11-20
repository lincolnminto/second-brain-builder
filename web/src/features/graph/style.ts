import type { Emphasis } from './selection';
import type { RenderNode } from './sync';

export type ColorBy = 'type' | 'community';
export type GraphPalette = {
  dark: boolean;
  background: string;
  surface: string;
  border: string;
  foreground: string;
  muted: string;
  types: Readonly<Record<string, string>>;
};

// Ten distinct hues per theme, close to the page type tokens, for communities.
export const COMMUNITY_COLORS = {
  light: ['#B9351C', '#16746B', '#5058A5', '#865B10', '#84517D', '#2F6E9E', '#587357', '#A3405F', '#526477', '#6E6A12'],
  dark: ['#FF7A50', '#72C5B9', '#ADB2F2', '#E4BD6F', '#CFA1C7', '#86BDEB', '#A8C49F', '#F09AB4', '#A1B1C2', '#D6CC7E'],
} as const;

/** The visible disc as a share of the node quad. The rest of the quad is glow. */
export const HALO_CORE = 0.56;
/** How long a live write makes its page pulse. */
export const PULSE_MS = 900;

/** The radius of the visible disc in pixels: it grows with the square root of the degree. */
export function nodeRadius(degree: number, compact: boolean): number {
  return compact ? 2.5 + Math.min(6, Math.sqrt(degree) * 1.2) : 3.5 + Math.min(10, Math.sqrt(degree) * 1.7);
}
/** The sigma node size: the disc plus its glow. */
export function nodeSize(degree: number, compact: boolean): number {
  return nodeRadius(degree, compact) / HALO_CORE;
}

function parseHex(color: string): [number, number, number] | null {
  const match = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(color.trim());
  if (!match) return null;
  const hex = match[1]!.length === 3 ? [...match[1]!].map(digit => digit + digit).join('') : match[1]!;
  return [0, 2, 4].map(offset => parseInt(hex.slice(offset, offset + 2), 16)) as [number, number, number];
}
const toHex = (value: number) => Math.round(Math.max(0, Math.min(255, value))).toString(16).padStart(2, '0');

/** `a` moved toward `b` by `weight` (0 keeps `a`). Unparseable colors give `a`. */
export function mixColors(a: string, b: string, weight: number): string {
  const from = parseHex(a);
  const to = parseHex(b);
  if (!from || !to) return a;
  return `#${from.map((channel, index) => toHex(channel + (to[index]! - channel) * weight)).join('')}`;
}
export function withAlpha(color: string, alpha: number): string {
  const rgb = parseHex(color);
  return rgb ? `#${rgb.map(toHex).join('')}${toHex(alpha * 255)}` : color;
}

/** Pulse strength from 0 to 1 and back, `age` milliseconds after a live write. */
export function pulse(age: number): number {
  const progress = age / PULSE_MS;
  return progress <= 0 || progress >= 1 ? 0 : Math.sin(Math.PI * progress) * (1 - progress * 0.5);
}

export type LookState = {
  palette: GraphPalette;
  colorBy: ColorBy;
  /** Color index per page; -1 for a page that has no community of its own. */
  communities: ReadonlyMap<string, number>;
  emphasis: Emphasis;
  now: number;
  reducedMotion: boolean;
};

export function nodeColor(key: string, node: Pick<RenderNode, 'type'>, state: Pick<LookState, 'palette' | 'colorBy' | 'communities'>): string {
  const { palette } = state;
  if (state.colorBy === 'community') {
    const colors = palette.dark ? COMMUNITY_COLORS.dark : COMMUNITY_COLORS.light;
    const index = state.communities.get(key) ?? -1;
    return index < 0 ? palette.muted : colors[index % colors.length]!;
  }
  return palette.types[node.type] || palette.muted;
}

export type NodeLook = {
  color: string;
  coreColor: string;
  glow: number;
  size: number;
  opacity: number;
  labelColor: string;
  labelVisibility: 'auto' | 'visible' | 'hidden';
  backdropVisibility: 'visible' | 'hidden';
  backdropBorderColor: string;
  depth: 'nodes' | 'topNodes';
  /** Labels draw at the node's depth, so a raised pill never covers its text. */
  labelDepth: 'nodes' | 'topNodes';
  zIndex: number;
};

/** How a node looks: ring color by type or community, a softer core, a glow,
 * and a label pill when it is selected, hovered, or a hub. When a page is
 * active (selected, or hovered without a selection), pages outside its
 * neighborhood fade. */
export function nodeLook(key: string, node: RenderNode, state: LookState): NodeLook {
  const { palette, emphasis } = state;
  const ring = nodeColor(key, node, state);
  const active = key === emphasis.active;
  const selected = key === emphasis.selected;
  const hovered = key === emphasis.hovered;
  // The hovered page never fades, so pointing at a page always names it.
  const faded = emphasis.active !== null && !emphasis.related.has(key) && !hovered;
  const neighbor = emphasis.active !== null && !active && emphasis.related.has(key);
  const boost = state.reducedMotion || node.pulseAt === undefined ? 0 : pulse(state.now - node.pulseAt);
  const base = palette.dark ? 0.5 : 0.34;
  const glow = Math.min(1, (active || hovered ? 0.95 : neighbor ? base + 0.2 : base) + 0.6 * boost);
  const pill = !faded && (selected || hovered || node.hub);
  const raised = active || hovered || neighbor;
  return {
    color: ring,
    coreColor: selected ? ring : mixColors(ring, palette.background, palette.dark ? 0.55 : 0.7),
    glow,
    size: node.size * (active ? 1.25 : 1) * (1 + 0.45 * boost),
    opacity: faded ? 0.16 : 1,
    labelColor: palette.foreground,
    labelVisibility: faded ? 'hidden' : pill || (neighbor && emphasis.related.size <= 24) ? 'visible' : 'auto',
    backdropVisibility: pill ? 'visible' : 'hidden',
    backdropBorderColor: selected ? ring : palette.border,
    depth: raised ? 'topNodes' : 'nodes',
    labelDepth: raised ? 'topNodes' : 'nodes',
    zIndex: active || hovered ? 3 : neighbor ? 2 : node.hub ? 1 : 0,
  };
}

export type EdgeLook = { opacity: number; size: number; depth: 'edges' | 'topEdges' };

/** Edges are faint gradients; the links of the active page stand out. */
export function edgeLook(source: string, target: string, state: Pick<LookState, 'palette' | 'emphasis'>): EdgeLook {
  const { active } = state.emphasis;
  if (active === null) return { opacity: state.palette.dark ? 0.34 : 0.3, size: 1, depth: 'edges' };
  return source === active || target === active
    ? { opacity: 0.9, size: 1.6, depth: 'topEdges' }
    : { opacity: 0.05, size: 0.8, depth: 'edges' };
}
