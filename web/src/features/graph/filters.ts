import type { Schema } from '../../api/client';
import { strings } from './strings';

export const PAGE_TYPES = ['source', 'summary', 'entity', 'concept', 'comparison', 'synthesis', 'name-index'] as const;
export type PageType = typeof PAGE_TYPES[number];

export function isPageType(type: string): type is PageType {
  return (PAGE_TYPES as readonly string[]).includes(type);
}
export function typeLabel(type: string): string {
  return isPageType(type) ? strings.pageTypes[type] : strings.unknown;
}

export function countTypes(nodes: readonly { type: string }[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const node of nodes) counts.set(node.type, (counts.get(node.type) ?? 0) + 1);
  return counts;
}

export type TypeChip = {
  type: string;
  label: string;
  count: number;
  known: boolean;
  /** No page has this type: the chip can only explain why. */
  disabled: boolean;
  hint: string | null;
};

/** The type filter chips: known types in a fixed order, then other types by
 * name. Types without pages show only with `showAll`, disabled and with a
 * hint, except the active type, which always shows so it can be cleared. */
export function typeChips(counts: ReadonlyMap<string, number>, options: { showAll: boolean; active: string | null }): TypeChip[] {
  const others = [...counts.keys()].filter(type => !isPageType(type)).sort();
  return [...PAGE_TYPES, ...others].flatMap(type => {
    const count = counts.get(type) ?? 0;
    const active = type === options.active;
    if (!count && !active && !options.showAll) return [];
    return [{
      type, label: typeLabel(type), count, known: isPageType(type), disabled: !count && !active,
      hint: count ? null : strings.emptyTypeHints[type] ?? strings.emptyTypeHint,
    }];
  });
}

/** True when the "All types" toggle has something to reveal. */
export function hasEmptyTypes(counts: ReadonlyMap<string, number>): boolean {
  return PAGE_TYPES.some(type => !counts.get(type));
}

export type FolderOption = { path: string; name: string; depth: number; count: number };

/** Every Folder below the root, depth first, for the Folder filter. */
export function folderOptions(root: Schema['FolderNode'] | undefined): FolderOption[] {
  const options: FolderOption[] = [];
  const visit = (folder: Schema['FolderNode'], depth: number) => {
    for (const child of [...folder.children ?? []].sort((a, b) => a.name.localeCompare(b.name))) {
      options.push({ path: child.path, name: child.name, depth, count: child.total_source_count });
      visit(child, depth + 1);
    }
  };
  if (root) visit(root, 0);
  return options;
}
