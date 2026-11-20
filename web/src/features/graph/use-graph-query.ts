import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api, result } from '../../api/client';

// Keys start with 'graph' and 'pages', so the job event feed refreshes them.

/** The graph of a Brain, or of one Folder (its Sources and the pages that cite them).
 * The previous graph stays while another Folder loads, so the layout does not reset. */
export function useGraphQuery(brainId: string, folder: string) {
  return useQuery({
    queryKey: ['graph', brainId, folder],
    queryFn: () => result(api.GET('/api/brains/{brain_id}/graph', { params: { path: { brain_id: brainId }, query: folder ? { folder } : {} } })),
    enabled: !!brainId,
    placeholderData: keepPreviousData,
  });
}

/** Page summaries of one type. The inspector reads Canonical Document
 * descriptions here, because a full Canonical Document can be very large. */
export function usePageSummaries(brainId: string, type: string, enabled: boolean) {
  return useQuery({
    queryKey: ['pages', brainId, 'graph-summaries', type],
    queryFn: () => result(api.GET('/api/brains/{brain_id}/pages', { params: { path: { brain_id: brainId }, query: { type: [type] } } })),
    enabled: !!brainId && enabled,
  });
}
