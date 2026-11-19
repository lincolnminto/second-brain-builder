import { expect, it } from 'vitest';
import { formatTokenEstimate, partProgressLabel, partProgressPercent } from './ingest';

it('formats large token estimates with K and M suffixes', () => {
  expect(formatTokenEstimate(950)).toBe('About 950 tokens');
  expect(formatTokenEstimate(12_300)).toBe('About 12 K tokens');
  expect(formatTokenEstimate(4_100_000)).toBe('About 4.1 M tokens');
});

it('builds the Part progress label and percent', () => {
  expect(partProgressLabel(12, 131)).toBe('Partly ingested · 12 of 131 Parts');
  expect(partProgressPercent(12, 131)).toBe(9);
  expect(partProgressPercent(0, 0)).toBe(0);
});