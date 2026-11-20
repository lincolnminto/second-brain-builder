import { expect, it } from 'vitest';
import { SOURCE_DRAG_TYPE, readSourceDrag, sourceDrop, type SourceDrag } from './folder-drag';

const paper: SourceDrag = { brainId: 'brain-1', slug: 'paper', folder: 'work', title: 'Paper' };
const data = (value: string) => ({ getData: (type: string) => type === SOURCE_DRAG_TYPE ? value : '' });

it('moves a dropped Source only into another Folder of the same Brain', () => {
  expect(sourceDrop(paper, 'brain-1', 'work/clients')).toEqual({ action: 'move', slug: 'paper', title: 'Paper', folder: 'work/clients' });
  expect(sourceDrop(paper, 'brain-1', '')).toEqual({ action: 'move', slug: 'paper', title: 'Paper', folder: '' });
  expect(sourceDrop(paper, 'brain-1', 'work')).toEqual({ action: 'ignore', reason: 'sameFolder', title: 'Paper' });
  expect(sourceDrop(paper, 'brain-2', 'archive')).toEqual({ action: 'ignore', reason: 'otherBrain', title: 'Paper' });
  expect(sourceDrop(null, 'brain-1', 'archive')).toEqual({ action: 'ignore', reason: 'notSource' });
});

it('reads only well-formed Source drag data', () => {
  expect(readSourceDrag(data(JSON.stringify(paper)))).toEqual(paper);
  expect(readSourceDrag(data(JSON.stringify({ ...paper, title: '' })))).toEqual({ ...paper, title: 'paper' });
  expect(readSourceDrag(data('not json'))).toBeNull();
  expect(readSourceDrag(data(JSON.stringify({ brainId: 'brain-1', folder: 'work' })))).toBeNull();
  expect(readSourceDrag(data(JSON.stringify([paper])))).toBeNull();
});
