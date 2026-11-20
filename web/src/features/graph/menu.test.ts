import { describe, expect, it, vi } from 'vitest';
import { menuItems, runMenuAction, type MenuHandlers } from './menu';

const entity = { path: 'wiki/entities/paul atreides.md', type: 'entity' };
const canonical = { path: 'sources/books/dune/dune.md', type: 'source' };

function handlers(overrides: Partial<MenuHandlers> = {}): MenuHandlers {
  return {
    brainId: 'brain-1', origin: 'https://brain.example.com',
    navigate: vi.fn(), focus: vi.fn(), showNeighbors: vi.fn(), setPinned: vi.fn(),
    copy: vi.fn(async () => undefined),
    ...overrides,
  };
}

describe('node menu', () => {
  it('opens a Canonical Document as its Source and a Wiki Page as a page, and offers Pin or Unpin by state', () => {
    expect(menuItems('brain-1', entity, false).map(item => item.action))
      .toEqual(['open', 'focus', 'neighbors-1', 'neighbors-2', 'pin', 'copy-link']);
    expect(menuItems('brain-1', canonical, true).map(item => [item.action, item.label])[0]).toEqual(['open-source', 'Open Source']);
    expect(menuItems('brain-1', canonical, true).map(item => item.action))
      .toEqual(['open-source', 'focus', 'neighbors-1', 'neighbors-2', 'unpin', 'copy-link']);
    // A source page outside the Source layout has no Source view to open.
    expect(menuItems('brain-1', { path: 'notes/odd.md', type: 'source' }, false).map(item => item.action)).not.toContain('open-source');
  });

  it('opens the Wiki Page, or the Source view for a Canonical Document (never its full body in the Wiki)', async () => {
    const actions = handlers();
    await runMenuAction('open', entity, actions);
    await runMenuAction('open-source', canonical, actions);
    await runMenuAction('open', canonical, actions);
    expect(actions.navigate).toHaveBeenNthCalledWith(1, '/brains/brain-1/wiki/wiki/entities/paul%20atreides.md');
    expect(actions.navigate).toHaveBeenNthCalledWith(2, '/brains/brain-1/sources/dune');
    expect(actions.navigate).toHaveBeenNthCalledWith(3, '/brains/brain-1/sources/dune');
  });

  it('focuses the neighborhood, scopes to depth 1 or 2, and pins or unpins', async () => {
    const actions = handlers();
    for (const action of ['focus', 'neighbors-1', 'neighbors-2', 'pin', 'unpin'] as const) {
      expect(await runMenuAction(action, entity, actions)).toBe('done');
    }
    expect(actions.focus).toHaveBeenCalledWith(entity.path);
    expect(actions.showNeighbors).toHaveBeenNthCalledWith(1, entity.path, 1);
    expect(actions.showNeighbors).toHaveBeenNthCalledWith(2, entity.path, 2);
    expect(actions.setPinned).toHaveBeenNthCalledWith(1, entity.path, true);
    expect(actions.setPinned).toHaveBeenNthCalledWith(2, entity.path, false);
    expect(actions.navigate).not.toHaveBeenCalled();
  });

  it('copies an absolute link to the page and reports a clipboard failure', async () => {
    const actions = handlers();
    expect(await runMenuAction('copy-link', entity, actions)).toBe('copied');
    expect(actions.copy).toHaveBeenCalledWith('https://brain.example.com/brains/brain-1/wiki/wiki/entities/paul%20atreides.md');
    await runMenuAction('copy-link', canonical, actions);
    expect(actions.copy).toHaveBeenLastCalledWith('https://brain.example.com/brains/brain-1/sources/dune');
    const denied = handlers({ copy: () => Promise.reject(new Error('Clipboard blocked')) });
    expect(await runMenuAction('copy-link', entity, denied)).toBe('copy-failed');
    const missing = handlers({ copy: () => { throw new TypeError('No clipboard'); } });
    expect(await runMenuAction('copy-link', entity, missing)).toBe('copy-failed');
  });
});
