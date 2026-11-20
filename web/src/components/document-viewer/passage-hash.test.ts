import { describe, expect, it } from 'vitest';
import { parsePassageHash, passageHash } from './passage-hash';

describe('parsePassageHash', () => {
  it.each([
    ['#passage=1200-1450', { char_start: 1200, char_end: 1450 }],
    ['passage=0-12', { char_start: 0, char_end: 12 }],
    ['#passage=30-30', { char_start: 30, char_end: 30 }],
    ['#from=wiki&passage=5-9', { char_start: 5, char_end: 9 }],
    ['#passage%3D5-9', null],
    ['#passage=7-9&passage=1-2', { char_start: 7, char_end: 9 }],
  ])('reads %s', (hash, expected) => {
    expect(parsePassageHash(hash)).toEqual(expected);
  });

  it.each(['', '#', '#passage=', '#passage=12', '#passage=20-10', '#passage=-1-5', '#passage=1.5-3', '#passage=a-b', '#passage=1-2-3', '#section-2'])(
    'rejects %j',
    hash => expect(parsePassageHash(hash)).toBeNull(),
  );
});

it('writes a hash that it reads back', () => {
  const range = { char_start: 98_765, char_end: 99_001 };
  expect(passageHash(range)).toBe('#passage=98765-99001');
  expect(parsePassageHash(passageHash(range))).toEqual(range);
});
