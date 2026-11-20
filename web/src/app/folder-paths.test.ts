import { describe, expect, it } from 'vitest';
import type { Schema } from '../api/client';
import { job } from '../test/fixtures';
import { deleteBlock, folderRows, nonSlugFolder, pendingFolderChanges, pendingPlacements, previewFolder, previewNewFolder, previewRename, slugify } from './folder-paths';

describe('slug preview', () => {
  // Expected values come from `second_brain_builder.brains.slugs.slugify`, so the preview matches the server.
  it.each([
    ['Client Notes', 'client-notes'],
    ['Crème Brûlée: A History', 'creme-brulee-a-history'],
    ['Don’t Panic', 'dont-panic'],
    ['Straße', 'strasse'],
    ['Œuvre ÆON', 'oeuvre-aeon'],
    ['  --Hello__World--  ', 'hello-world'],
    ['São Paulo · 2026', 'sao-paulo-2026'],
    ['日本語', 'untitled'],
    ['index', 'index-1'],
    [`${'a'.repeat(30)} ${'b'.repeat(30)} ${'c'.repeat(30)}`, `${'a'.repeat(30)}-${'b'.repeat(30)}`],
  ])('slugifies %j like the server', (text, slug) => {
    expect(slugify(text)).toBe(slug);
  });

  it('turns each part of a nested name into a slug inside the parent', () => {
    expect(previewFolder('Work/Client Notes')).toEqual({ ok: true, path: 'work/client-notes' });
    expect(previewFolder(' / Q3 // Notes / ', 'work')).toEqual({ ok: true, path: 'work/q3/notes' });
  });

  it.each([
    ['', 'empty'],
    [' / ', 'empty'],
    ['../secrets', 'dotPart'],
    ['.hidden', 'dotPart'],
    ['a\\b', 'invalidCharacter'],
    ['a/b/c/d/e/f/g/h/i', 'tooDeep'],
    [Array.from({ length: 8 }, () => 'x'.repeat(70)).join('/'), 'tooLong'],
  ])('rejects %j before the request', (name, problem) => {
    expect(previewFolder(name)).toEqual({ ok: false, problem });
  });

  it('flags a new Folder that exists already', () => {
    expect(previewNewFolder('Clients', 'work', new Set(['work', 'work/clients']))).toEqual({ ok: false, problem: 'exists' });
  });

  it('keeps a renamed Folder in its parent and refuses no-op or circular names', () => {
    const existing = new Set(['work', 'work/clients', 'work/archive']);
    expect(previewRename('Customers', 'work/clients', existing)).toEqual({ ok: true, path: 'work/customers' });
    expect(previewRename('clients', 'work/clients', existing)).toEqual({ ok: false, problem: 'unchanged' });
    expect(previewRename('Archive', 'work/clients', existing)).toEqual({ ok: false, problem: 'exists' });
    expect(previewRename('work/inner', 'work', existing)).toEqual({ ok: false, problem: 'intoItself' });
  });

  it('treats the unchanged name of a Folder that is not a slug as unchanged', () => {
    // As it is, `Field Notes` gives the path `field-notes`, which differs from the Folder.
    expect(previewRename('Field Notes', 'Field Notes', new Set(['Field Notes']))).toEqual({ ok: false, problem: 'unchanged' });
    expect(previewRename(' Field Notes ', 'Field Notes', new Set(['Field Notes']))).toEqual({ ok: false, problem: 'unchanged' });
    expect(previewRename('Field notes', 'Field Notes', new Set(['Field Notes']))).toEqual({ ok: true, path: 'field-notes' });
  });

  it('finds the first Folder on a path whose name the server would turn into a slug', () => {
    expect(nonSlugFolder('Field Notes/sub')).toBe('Field Notes');
    expect(nonSlugFolder('work/Client Notes/2026')).toBe('work/Client Notes');
    expect(nonSlugFolder('work/index')).toBe('work/index');
    expect(nonSlugFolder('work/clients')).toBeUndefined();
    expect(nonSlugFolder('')).toBeUndefined();
  });
});

const active = (id: number, kind: Schema['JobKind'], payload: Record<string, unknown>, status: Schema['JobStatus'] = 'queued'): Schema['JobView'] =>
  ({ ...job, id, kind, status, payload: { brain_id: job.brain_id, ...payload } });

describe('delete rule', () => {
  it('allows only a Folder without Sources, also none on the way in', () => {
    expect(deleteBlock({ path: 'archive', count: 0 }, [])).toBeNull();
    expect(deleteBlock({ path: 'work', count: 3 }, [])).toEqual({ reason: 'hasSources', count: 3 });
    const placements = pendingPlacements([active(4, 'source.add', { folder: 'archive/2025' }), active(5, 'source.move', { folder: 'other' }, 'succeeded')]);
    expect(placements).toEqual(['archive/2025']);
    expect(deleteBlock({ path: 'archive', count: 0 }, placements)).toEqual({ reason: 'sourcesArriving' });
    expect(deleteBlock({ path: 'archive-old', count: 0 }, placements)).toBeNull();
  });
});

describe('pending Folder jobs', () => {
  it('shows a Folder that is being created before the job ends, and marks renames', () => {
    const tree: Schema['FolderNode'] = { name: '', path: '', source_count: 0, total_source_count: 1, children: [
      { name: 'work', path: 'work', source_count: 1, total_source_count: 1, children: [] },
    ] };
    const changes = pendingFolderChanges([
      active(7, 'folder.create', { folder: 'drafts/ideas' }),
      active(8, 'folder.rename', { folder: 'work', new_folder: 'projects' }, 'running'),
      active(9, 'folder.create', { folder: 'done' }, 'succeeded'),
    ]);
    const rows = folderRows(tree, changes);
    expect(rows.map(row => [row.path, row.ghost, row.pending?.kind])).toEqual([['drafts', true, 'create'], ['work', false, 'rename']]);
    expect(rows[0]?.children.map(row => row.path)).toEqual(['drafts/ideas']);
  });
});
