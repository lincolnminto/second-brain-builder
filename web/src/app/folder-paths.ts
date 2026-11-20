import type { Schema } from '../api/client';

// The Folder rules of the server (`brains/slugs.py` and `normalize_folder` in
// `services/_common.py`), so the inline input can show the slug path before the
// request. The server stays the authority: the tree shows what the job made.
export const MAX_FOLDER_DEPTH = 8;
export const MAX_FOLDER_LENGTH = 512;
const SLUG_MAX_LENGTH = 80;
const FALLBACK_SLUG = 'untitled';
const RESERVED_SLUGS = new Set(['index', 'log', 'assets']);
const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const TRANSLITERATIONS: Record<string, string> = {
  'ß': 'ss', 'ẞ': 'SS', 'æ': 'ae', 'Æ': 'AE', 'œ': 'oe', 'Œ': 'OE', 'ø': 'o', 'Ø': 'O',
  'đ': 'd', 'Đ': 'D', 'ð': 'd', 'Ð': 'D', 'þ': 'th', 'Þ': 'TH', 'ł': 'l', 'Ł': 'L', 'ı': 'i',
};
const APOSTROPHES = new Set(["'", '’', 'ʼ']);
// Python drops characters with a combining class; nonspacing marks are the close match here.
const COMBINING_MARK = /\p{Mn}/u;

export function isSlug(text: string) {
  return SLUG.test(text) && !RESERVED_SLUGS.has(text);
}

export function slugify(text: string) {
  const folded = Array.from(text, char => TRANSLITERATIONS[char] ?? char).join('').normalize('NFKD');
  let kept = '';
  for (const char of folded) {
    if (APOSTROPHES.has(char) || COMBINING_MARK.test(char)) continue;
    // Other characters outside ASCII (CJK, dashes, symbols) separate words.
    kept += (char.codePointAt(0) ?? 0) < 128 ? char.toLowerCase() : '-';
  }
  let slug = trimHyphens(kept.replace(/[^a-z0-9]+/g, '-'));
  if (slug.length > SLUG_MAX_LENGTH) {
    const cut = slug.slice(0, SLUG_MAX_LENGTH + 1);
    slug = trimHyphens(cut.includes('-') ? cut.slice(0, cut.lastIndexOf('-')) : cut.slice(0, SLUG_MAX_LENGTH));
  }
  if (!slug) return FALLBACK_SLUG;
  return RESERVED_SLUGS.has(slug) ? `${slug}-1` : slug;
}

function trimHyphens(text: string) {
  return text.replace(/^-+|-+$/g, '');
}

export type FolderProblem = 'empty' | 'invalidCharacter' | 'dotPart' | 'tooDeep' | 'tooLong' | 'exists' | 'unchanged' | 'intoItself';
export type FolderPreview = { ok: true; path: string } | { ok: false; problem: FolderProblem };

/** The slug path that a typed Folder name gives inside `parent` (`''` is the Brain root). */
export function previewFolder(name: string, parent = ''): FolderPreview {
  if (/[\\\0]/.test(name)) return { ok: false, problem: 'invalidCharacter' };
  const named = splitParts(name);
  if (!named.length) return { ok: false, problem: 'empty' };
  const parts = [...splitParts(parent), ...named];
  if (parts.some(part => part.startsWith('.'))) return { ok: false, problem: 'dotPart' };
  const slugs = parts.map(part => isSlug(part) ? part : slugify(part));
  if (slugs.length > MAX_FOLDER_DEPTH) return { ok: false, problem: 'tooDeep' };
  const path = slugs.join('/');
  if (path.length > MAX_FOLDER_LENGTH) return { ok: false, problem: 'tooLong' };
  return { ok: true, path };
}

function splitParts(value: string) {
  return value.split('/').map(part => part.trim()).filter(Boolean);
}

/** The preview of a new Folder, checked against the Folders of the Brain. */
export function previewNewFolder(name: string, parent: string, existing: ReadonlySet<string>): FolderPreview {
  const preview = previewFolder(name, parent);
  if (preview.ok && existing.has(preview.path)) return { ok: false, problem: 'exists' };
  return preview;
}

/** The preview of a rename: the new name stays in the parent of `folder`. */
export function previewRename(name: string, folder: string, existing: ReadonlySet<string>): FolderPreview {
  // Compare the typed name, not the slug path: `Field Notes` as it is gives `field-notes`.
  if (splitParts(name).join('/') === folderName(folder)) return { ok: false, problem: 'unchanged' };
  const preview = previewFolder(name, parentFolder(folder));
  if (!preview.ok) return preview;
  if (preview.path === folder) return { ok: false, problem: 'unchanged' };
  if (within(preview.path, folder)) return { ok: false, problem: 'intoItself' };
  if (existing.has(preview.path)) return { ok: false, problem: 'exists' };
  return preview;
}

/**
 * The first Folder on `folder` (the Folder itself or one above it) whose name is not
 * a slug, such as `Field Notes` pushed from GitHub. The server turns each part of a
 * Folder in an upload, a move, or a new Folder into a slug, so whatever goes into
 * `Field Notes` lands in `field-notes`. Only a rename of that Folder itself works.
 */
export function nonSlugFolder(folder: string): string | undefined {
  const parts = folder ? folder.split('/') : [];
  const index = parts.findIndex(part => !isSlug(part));
  return index < 0 ? undefined : parts.slice(0, index + 1).join('/');
}

/** The Brain home URL of a Folder; `''` is All Sources. */
export function folderUrl(root: string, folder: string) {
  return folder ? `${root}?folder=${encodeURIComponent(folder)}` : root;
}

export function parentFolder(folder: string) {
  const index = folder.lastIndexOf('/');
  return index < 0 ? '' : folder.slice(0, index);
}

export function folderName(folder: string) {
  return folder.slice(folder.lastIndexOf('/') + 1);
}

/** True when `folder` is `other` or one of its subfolders. */
export function within(folder: string, other: string) {
  return folder === other || folder.startsWith(`${other}/`);
}

export function folderPaths(root: Schema['FolderNode'] | undefined): Set<string> {
  const paths = new Set<string>();
  const visit = (node: Schema['FolderNode']) => { if (node.path) paths.add(node.path); node.children?.forEach(visit); };
  if (root) visit(root);
  return paths;
}

const ACTIVE: ReadonlySet<Schema['JobStatus']> = new Set(['queued', 'running']);

function activeJobs(jobs: readonly Schema['JobView'][] | undefined) {
  return (jobs ?? []).filter(job => ACTIVE.has(job.status));
}

function text(value: unknown) {
  return typeof value === 'string' ? value : undefined;
}

export type FolderChange = { jobId: number; kind: 'create' | 'rename' | 'delete'; folder: string; newFolder?: string };

/** The Folder jobs that wait or run, from their payloads. */
export function pendingFolderChanges(jobs: readonly Schema['JobView'][] | undefined): FolderChange[] {
  const changes: FolderChange[] = [];
  for (const job of activeJobs(jobs)) {
    const folder = text(job.payload.folder);
    if (!folder) continue;
    if (job.kind === 'folder.create') changes.push({ jobId: job.id, kind: 'create', folder });
    if (job.kind === 'folder.delete') changes.push({ jobId: job.id, kind: 'delete', folder });
    const newFolder = text(job.payload.new_folder);
    if (job.kind === 'folder.rename' && newFolder) changes.push({ jobId: job.id, kind: 'rename', folder, newFolder });
  }
  return changes;
}

/** The pending Folder change that blocks other changes to `folder`, if one does. */
export function blockingChange(folder: string, changes: readonly FolderChange[]) {
  const overlap = (path: string | undefined) => path !== undefined && (within(path, folder) || within(folder, path));
  return changes.find(change => overlap(change.folder) || overlap(change.newFolder));
}

/** The Folders that pending uploads and moves put a Source into. */
export function pendingPlacements(jobs: readonly Schema['JobView'][] | undefined): string[] {
  return activeJobs(jobs).flatMap(job => {
    const folder = text(job.payload.folder);
    return (job.kind === 'source.add' || job.kind === 'source.move') && folder !== undefined ? [folder] : [];
  });
}

/** The pending move of one Source: the Folder it goes to. */
export function pendingMove(slug: string, jobs: readonly Schema['JobView'][] | undefined) {
  const job = activeJobs(jobs).find(item => item.kind === 'source.move' && item.payload.source_slug === slug);
  return job ? text(job.payload.folder) ?? '' : undefined;
}

export type DeleteBlock = { reason: 'hasSources'; count: number } | { reason: 'sourcesArriving' };

/**
 * Why the Folder cannot be deleted, or `null` when it can. Only a Folder without
 * Sources can go: none in it or its subfolders, and none on the way in.
 */
export function deleteBlock(folder: { path: string; count: number }, placements: readonly string[]): DeleteBlock | null {
  if (folder.count > 0) return { reason: 'hasSources', count: folder.count };
  if (placements.some(target => within(target, folder.path))) return { reason: 'sourcesArriving' };
  return null;
}

export type FolderRow = {
  name: string; path: string; children: FolderRow[];
  /** The Sources in the Folder and its subfolders. */
  count: number;
  /** A Folder that a pending `folder.create` makes; it is not in the Brain repo yet. */
  ghost: boolean;
  pending?: FolderChange;
};

/** The tree for the sidebar: the Folders, with pending jobs shown on their rows. */
export function folderRows(root: Schema['FolderNode'] | undefined, changes: readonly FolderChange[]): FolderRow[] {
  const row = (node: Schema['FolderNode']): FolderRow => ({
    name: node.name, path: node.path, count: node.total_source_count, ghost: false,
    children: (node.children ?? []).map(row),
    pending: changes.find(change => change.folder === node.path),
  });
  const rows = (root?.children ?? []).map(row);
  for (const change of changes) if (change.kind === 'create') addGhost(rows, change);
  return rows;
}

function addGhost(rows: FolderRow[], change: FolderChange) {
  const parts = change.folder.split('/');
  let level = rows;
  for (const [index, name] of parts.entries()) {
    const path = parts.slice(0, index + 1).join('/');
    let node = level.find(item => item.path === path);
    if (!node) {
      node = { name, path, count: 0, children: [], ghost: true, pending: change };
      level.push(node);
      level.sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0); // the server's order
    }
    level = node.children;
  }
}
