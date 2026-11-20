import { useEffect, useRef } from 'react';
import { useQueries, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { api, result, type Schema } from '../../api/client';
import { SEGMENT_PAGE_SIZE } from './ranges';
import type { DocumentOutline, DocumentSegment } from './types';

type SegmentsResponse = { segments: DocumentSegment[] };
type JobEvent = Schema['JobEventView'];

/** The state of one page of segments, in the order of the requested pages. */
export interface SegmentPageState {
  status: 'pending' | 'error' | 'success';
  error: unknown;
  retry: () => void;
}

export interface LoadedSegments {
  /** Every loaded segment by index. A segment object stays the same while its page is cached. */
  segments: Map<number, DocumentSegment>;
  pages: SegmentPageState[];
}

// Not under the 'page' key: job events invalidate 'page' queries, and a large
// document must not reload on every progress event.
export function useDocumentOutline(brainId: string, path: string) {
  return useQuery({
    queryKey: ['document-outline', brainId, path],
    queryFn: () => result(api.GET('/api/brains/{brain_id}/documents/outline', { params: { path: { brain_id: brainId }, query: { path } } })),
    enabled: !!brainId && !!path,
  });
}

/** A key part that changes when the document changes size, so stale segments are not reused. */
export function outlineVersion(outline: DocumentOutline): string {
  return `${outline.total_chars}:${outline.segments}`;
}

function combine(results: UseQueryResult<SegmentsResponse>[]): LoadedSegments {
  const segments = new Map<number, DocumentSegment>();
  for (const page of results) for (const segment of page.data?.segments ?? []) segments.set(segment.index, segment);
  return {
    segments,
    pages: results.map(page => ({ status: page.status, error: page.error, retry: () => void page.refetch() })),
  };
}

/** Load pages of `SEGMENT_PAGE_SIZE` segments; page `n` holds segments `n * SEGMENT_PAGE_SIZE` and up. */
export function useSegmentPages(brainId: string, path: string, version: string, pages: readonly number[]): LoadedSegments {
  return useQueries({
    queries: pages.map(page => ({
      queryKey: ['document-segments', brainId, path, version, page],
      queryFn: () => result(api.GET('/api/brains/{brain_id}/documents/segments', {
        params: { path: { brain_id: brainId }, query: { path, from: page * SEGMENT_PAGE_SIZE, count: SEGMENT_PAGE_SIZE } },
      })),
      // A page refreshes when the viewer mounts again (once stale) and when a job
      // writes the page (useReloadOnPageWrite), not on focus or reconnect: a long
      // read has loaded many pages.
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
    })),
    combine,
  });
}

/** The id of the newest `page_written` or `page_deleted` event for `path` in a job event feed, or null. */
export function lastPageWrite(events: readonly JobEvent[], path: string): number | null {
  let newest: number | null = null;
  for (const event of events) {
    const write = event.kind === 'page_written' || event.kind === 'page_deleted';
    if (write && event.data.path === path && (newest === null || event.id > newest)) newest = event.id;
  }
  return newest;
}

/**
 * Reload the cached outline and segments of a page when a job writes the page,
 * for example when a Reconvert writes the Canonical Document again.
 *
 * Job events refresh the 'page' queries, not these ones (see `useDocumentOutline`).
 * Mount this where it stays mounted while the viewer itself is hidden (a Source
 * that is converting), so the cache is marked stale and the viewer loads the new
 * text when it shows again. A write that is already in the feed at mount also
 * reloads once, because the cache can hold text from before it.
 */
export function useReloadOnPageWrite(brainId: string, path: string, events: readonly JobEvent[]): void {
  const cache = useQueryClient();
  const newest = lastPageWrite(events, path);
  const handled = useRef<number | null>(null);
  useEffect(() => {
    if (newest === null || newest === handled.current) return;
    handled.current = newest;
    void cache.invalidateQueries({ queryKey: ['document-outline', brainId, path] });
    void cache.invalidateQueries({ queryKey: ['document-segments', brainId, path] });
  }, [cache, brainId, path, newest]);
}
