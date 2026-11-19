import { expect, it } from 'vitest';
import { shouldAutoFit } from './autofit';

it('fits initial nodes and live additions until the user moves the viewport', () => {
  expect(shouldAutoFit(false, new Set(), ['a'])).toBe(true);
  expect(shouldAutoFit(false, new Set(['a']), ['a', 'b'])).toBe(true);
  expect(shouldAutoFit(false, new Set(['a']), ['a'])).toBe(false);
  expect(shouldAutoFit(false, new Set(['a', 'b']), ['b'])).toBe(false);
  expect(shouldAutoFit(false, new Set(), [])).toBe(false);
  expect(shouldAutoFit(true, new Set(['a']), ['a', 'b'])).toBe(false);
  expect(shouldAutoFit(true, new Set(), ['a'])).toBe(false);
});
