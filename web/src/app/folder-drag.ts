import { useState, type DragEvent } from 'react';

/** The drag data type of a Source row; the sidebar Folders accept it. */
export const SOURCE_DRAG_TYPE = 'application/x-sbb-source';

export type SourceDrag = { brainId: string; slug: string; folder: string; title: string };

export function writeSourceDrag(data: DataTransfer, source: SourceDrag) {
  data.setData(SOURCE_DRAG_TYPE, JSON.stringify(source));
  data.setData('text/plain', source.title);
  data.effectAllowed = 'move';
}

/** True while a Source is dragged. Browsers hide the data until the drop, but not its types. */
export function hasSourceDrag(data: Pick<DataTransfer, 'types'> | null) {
  return !!data && Array.from(data.types).includes(SOURCE_DRAG_TYPE);
}

export function readSourceDrag(data: Pick<DataTransfer, 'getData'> | null): SourceDrag | null {
  if (!data) return null;
  try {
    const value: unknown = JSON.parse(data.getData(SOURCE_DRAG_TYPE));
    if (typeof value !== 'object' || value === null) return null;
    const { brainId, slug, folder, title } = value as Record<string, unknown>;
    if (typeof brainId !== 'string' || typeof slug !== 'string' || !slug || typeof folder !== 'string') return null;
    return { brainId, slug, folder, title: typeof title === 'string' && title ? title : slug };
  } catch {
    return null; // Not our data, or another app's malformed data.
  }
}

export type SourceDrop =
  | { action: 'move'; slug: string; title: string; folder: string }
  | { action: 'ignore'; reason: 'notSource' | 'otherBrain' | 'sameFolder'; title?: string };

/** What a drop of `drag` on the Folder `target` (`''` is the Brain root) does. */
export function sourceDrop(drag: SourceDrag | null, brainId: string, target: string): SourceDrop {
  if (!drag) return { action: 'ignore', reason: 'notSource' };
  if (drag.brainId !== brainId) return { action: 'ignore', reason: 'otherBrain', title: drag.title };
  if (drag.folder === target) return { action: 'ignore', reason: 'sameFolder', title: drag.title };
  return { action: 'move', slug: drag.slug, title: drag.title, folder: target };
}

/** Drop target props for one Folder row, and whether a Source is over it. */
export function useSourceDropTarget(enabled: boolean, onDrop: (drag: SourceDrag | null) => void) {
  const [over, setOver] = useState(false);
  const props = {
    onDragOver(event: DragEvent<HTMLElement>) {
      if (!enabled || !hasSourceDrag(event.dataTransfer)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      setOver(true);
    },
    onDragLeave(event: DragEvent<HTMLElement>) {
      if (!(event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget))) setOver(false);
    },
    onDrop(event: DragEvent<HTMLElement>) {
      setOver(false);
      if (!enabled || !hasSourceDrag(event.dataTransfer)) return;
      event.preventDefault();
      onDrop(readSourceDrag(event.dataTransfer));
    },
  };
  return { over: over && enabled, props };
}
