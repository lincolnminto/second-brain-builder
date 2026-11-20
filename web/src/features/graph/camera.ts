import type { Point } from './sync';

export type Bounds = { minX: number; maxX: number; minY: number; maxY: number };

export function boundsOf(points: Iterable<Point>): Bounds | null {
  let bounds: Bounds | null = null;
  for (const { x, y } of points) {
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    bounds = bounds
      ? { minX: Math.min(bounds.minX, x), maxX: Math.max(bounds.maxX, x), minY: Math.min(bounds.minY, y), maxY: Math.max(bounds.maxY, y) }
      : { minX: x, maxX: x, minY: y, maxY: y };
  }
  return bounds;
}

/** The camera state (in sigma's framed coordinates) that shows `bounds`.
 * `unit` is how many pixels one framed unit covers at camera ratio 1. */
export function fitCamera(bounds: Bounds, unit: Point, viewport: { width: number; height: number }, padding: number, limits: { min: number; max: number }) {
  const width = Math.max(1, viewport.width - 2 * padding);
  const height = Math.max(1, viewport.height - 2 * padding);
  const ratio = Math.max((bounds.maxX - bounds.minX) * unit.x / width, (bounds.maxY - bounds.minY) * unit.y / height);
  return {
    x: (bounds.minX + bounds.maxX) / 2,
    y: (bounds.minY + bounds.maxY) / 2,
    ratio: Math.min(limits.max, Math.max(limits.min, ratio)),
    angle: 0,
  };
}

/** Time after an app camera animation in which camera updates still count as the app's. */
export const CAMERA_GRACE_MS = 100;

/** Autofit: while it is on, the graph rescales to fit the view as the layout
 * moves. It ends for good when the user moves the camera. Camera updates
 * during an animation that the app started do not count as the user's. */
export class Autofit {
  private ended = false;
  private appUntil = -Infinity;

  get active() { return !this.ended; }

  /** The app animates the camera for `duration` milliseconds from `now`. */
  appMove(now: number, duration: number) {
    this.appUntil = Math.max(this.appUntil, now + duration + CAMERA_GRACE_MS);
  }

  /** The camera changed. True when this ends autofit. */
  cameraUpdated(now: number): boolean {
    return now > this.appUntil && this.end();
  }

  /** End autofit, for an explicit camera action (zoom, pan, fit, focus, drag).
   * True when autofit was still on. */
  end(): boolean {
    if (this.ended) return false;
    this.ended = true;
    return true;
  }
}
