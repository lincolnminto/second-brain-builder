import { expect, it } from 'vitest';
import { DEFAULT_INGEST_MAX_TOKENS, formatTokenEstimate, needsIngestDialog, partProgressLabel, partProgressPercent } from './ingest';
import { t } from './strings';

it('pluralizes the remaining Parts in the estimate dialog', () => {
  expect(t('continueHint', { remaining: 1, tokens: 'About 150 tokens' })).toBe('1 Part remains. About 150 tokens');
  expect(t('continueHint', { remaining: 4, tokens: 'About 600 tokens' })).toBe('4 Parts remain. About 600 tokens');
});

it('asks for a run limit for more than ten Parts or an estimate over the default', () => {
  expect(needsIngestDialog(11, 1)).toBe(true);
  expect(needsIngestDialog(10, DEFAULT_INGEST_MAX_TOKENS)).toBe(false);
  expect(needsIngestDialog(1, DEFAULT_INGEST_MAX_TOKENS + 1)).toBe(true);
  expect(needsIngestDialog(null, DEFAULT_INGEST_MAX_TOKENS + 1)).toBe(true);
  expect(needsIngestDialog(null, 1)).toBe(false);
});

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
