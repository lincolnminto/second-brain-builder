import { describe, expect, it } from 'vitest';
import type { Schema } from '../../api/client';
import { countTypes, folderOptions, hasEmptyTypes, PAGE_TYPES, typeChips } from './filters';

const counts = countTypes([
  { type: 'source' }, { type: 'summary' }, { type: 'entity' }, { type: 'entity' }, { type: 'zeta' }, { type: 'alpha' },
]);

describe('type filter chips', () => {
  it('shows only types with pages, known types first, then other types by name', () => {
    const chips = typeChips(counts, { showAll: false, active: null });
    expect(chips.map(chip => [chip.type, chip.count])).toEqual([['source', 1], ['summary', 1], ['entity', 2], ['alpha', 1], ['zeta', 1]]);
    expect(chips.every(chip => !chip.disabled && chip.hint === null)).toBe(true);
    expect(chips.find(chip => chip.type === 'zeta')).toMatchObject({ known: false, label: 'Other' });
  });

  it('with All types, adds the empty known types, disabled, with a hint that explains them', () => {
    const chips = typeChips(counts, { showAll: true, active: null });
    expect(chips.map(chip => chip.type)).toEqual([...PAGE_TYPES, 'alpha', 'zeta']);
    const empty = Object.fromEntries(chips.filter(chip => chip.disabled).map(chip => [chip.type, chip.hint]));
    expect(empty).toEqual({
      concept: 'No pages of this type yet',
      comparison: 'Ingest writes comparison pages when a Source compares things',
      synthesis: 'Synthesis pages come from answers saved by you or your agents',
      'name-index': 'Name Index pages list minor names of large Sources',
    });
  });

  it('keeps an active type visible and enabled when it has no pages, so it can be cleared', () => {
    const chips = typeChips(counts, { showAll: false, active: 'synthesis' });
    expect(chips.find(chip => chip.type === 'synthesis')).toMatchObject({ count: 0, disabled: false });
    expect(chips.some(chip => chip.type === 'comparison')).toBe(false);
  });

  it('offers the All types toggle only while a known type has no pages', () => {
    expect(hasEmptyTypes(counts)).toBe(true);
    expect(hasEmptyTypes(countTypes(PAGE_TYPES.map(type => ({ type }))))).toBe(false);
  });
});

describe('Folder filter options', () => {
  it('lists nested Folders depth first, by name, without the root', () => {
    const folder = (path: string, total: number, children: Schema['FolderNode'][] = []): Schema['FolderNode'] =>
      ({ name: path.split('/').at(-1)!, path, source_count: 0, total_source_count: total, children });
    const root = { ...folder('', 5, [folder('work', 3, [folder('work/clients', 2), folder('work/archive', 0)]), folder('personal', 2)]), name: '' };
    expect(folderOptions(root)).toEqual([
      { path: 'personal', name: 'personal', depth: 0, count: 2 },
      { path: 'work', name: 'work', depth: 0, count: 3 },
      { path: 'work/archive', name: 'archive', depth: 1, count: 0 },
      { path: 'work/clients', name: 'clients', depth: 1, count: 2 },
    ]);
    expect(folderOptions(undefined)).toEqual([]);
  });
});
