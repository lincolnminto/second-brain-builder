import { describe, expect, it } from 'vitest';
import { Autofit, boundsOf, CAMERA_GRACE_MS, fitCamera } from './camera';

describe('autofit', () => {
  it('lasts until the user moves the camera, not while the app animates it', () => {
    const autofit = new Autofit();
    autofit.appMove(1000, 450);
    expect(autofit.cameraUpdated(1200)).toBe(false);
    expect(autofit.cameraUpdated(1450 + CAMERA_GRACE_MS)).toBe(false);
    expect(autofit.active).toBe(true);
    expect(autofit.cameraUpdated(1451 + CAMERA_GRACE_MS)).toBe(true);
    expect(autofit.active).toBe(false);
    // It ends once: later moves and actions do not end it again.
    expect(autofit.cameraUpdated(3000)).toBe(false);
    expect(autofit.end()).toBe(false);
  });

  it('ends at once for an explicit camera action', () => {
    const autofit = new Autofit();
    expect(autofit.end()).toBe(true);
    expect(autofit.active).toBe(false);
  });
});

describe('camera framing', () => {
  it('frames the bounds of finite points inside the padding, within the zoom limits', () => {
    const bounds = boundsOf([{ x: 0, y: 1 }, { x: 2, y: 0 }, { x: Number.NaN, y: 5 }]);
    expect(bounds).toEqual({ minX: 0, maxX: 2, minY: 0, maxY: 1 });
    expect(boundsOf([])).toBeNull();
    const unit = { x: 100, y: 100 };
    const viewport = { width: 300, height: 200 };
    expect(fitCamera(bounds!, unit, viewport, 50, { min: 0.1, max: 4 })).toEqual({ x: 1, y: 0.5, ratio: 1, angle: 0 });
    expect(fitCamera(bounds!, unit, viewport, 50, { min: 0.1, max: 0.5 }).ratio).toBe(0.5);
    // One page has no extent: the zoom-in limit applies.
    expect(fitCamera({ minX: 3, maxX: 3, minY: 4, maxY: 4 }, unit, viewport, 50, { min: 0.25, max: 4 })).toEqual({ x: 3, y: 4, ratio: 0.25, angle: 0 });
  });
});
