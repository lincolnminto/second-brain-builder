import { keepPreviousData, queryOptions, useQuery } from '@tanstack/react-query';
import { api, result } from '../../api/client';

/** The passage that footnote reference number `ref` (from 0) of a Wiki Page cites.
 * The key starts with the page's key, so a change to the page refreshes it. */
export function citationQuery(brainId: string, page: string, ref: number) {
  return queryOptions({
    queryKey: ['page', brainId, page, 'citation', ref],
    queryFn: () => result(api.GET('/api/brains/{brain_id}/citations/resolve', { params: { path: { brain_id: brainId }, query: { page, ref } } })),
    staleTime: 5 * 60_000,
  });
}

/** The pages of the Brain, or of one Folder: its Sources and the pages that cite them. */
export function useWikiPages(brainId: string, folder: string) {
  return useQuery({
    queryKey: ['pages', brainId, undefined, folder],
    queryFn: () => result(api.GET('/api/brains/{brain_id}/pages', { params: { path: { brain_id: brainId }, query: { folder: folder || undefined } } })),
    enabled: !!brainId, placeholderData: keepPreviousData,
  });
}
