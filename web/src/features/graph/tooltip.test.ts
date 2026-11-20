import { describe, expect, it } from 'vitest';
import { TOOLTIP_MAX_WIDTH, tooltipPlacement } from './tooltip';

describe('type chip tooltips', () => {
  it('start at their chip when they fit', () => {
    expect(tooltipPlacement(20, 200, { left: 0, right: 900 })).toEqual({ offset: 0, maxWidth: TOOLTIP_MAX_WIDTH });
  });

  it('move left to stay inside a narrow frame, for example on a phone', () => {
    // A 343px frame, a chip at 150px, and a tooltip at its full 260px.
    const { offset, maxWidth } = tooltipPlacement(150, 260, { left: 0, right: 343 });
    expect(maxWidth).toBe(260);
    expect(150 + offset).toBe(75);
    expect(150 + offset + 260).toBeLessThanOrEqual(343 - 8);
  });

  it('get narrower than the frame when the frame is narrower than a tooltip', () => {
    expect(tooltipPlacement(50, 260, { left: 0, right: 200 })).toEqual({ offset: -42, maxWidth: 184 });
  });
});
