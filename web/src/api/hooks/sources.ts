import { useMutation, useQuery } from '@tanstack/react-query';
import { api, result } from '../client';
import { uploadSource, type UploadOptions } from '../upload';
import { useInvalidate } from './shared';

type SourceId = { brainId: string; slug: string };
export function useFolders(brainId: string) {
  return useQuery({ queryKey: ['folders', brainId], queryFn: () => result(api.GET('/api/brains/{brain_id}/folders', { params: { path: { brain_id: brainId } } })), enabled: !!brainId });
}
export function useSources(brainId: string, folder?: string, recursive = true) {
  return useQuery({ queryKey: ['sources', brainId, folder, recursive], queryFn: () => result(api.GET('/api/brains/{brain_id}/sources', { params: { path: { brain_id: brainId }, query: { folder, recursive } } })), enabled: !!brainId });
}
export function useSource(brainId: string, slug: string) {
  return useQuery({ queryKey: ['source', brainId, slug], queryFn: () => result(api.GET('/api/brains/{brain_id}/sources/{slug}', { params: { path: { brain_id: brainId, slug } } })), enabled: !!brainId && !!slug });
}
export function useUploadSource() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: (options: UploadOptions) => uploadSource(options), onSuccess: () => invalidate(['sources'], ['folders'], ['jobs'], ['brains'], ['lfs-usage']) });
}
export function useMoveSource() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: ({ brainId, slug, folder }: SourceId & { folder: string }) => result(api.POST('/api/brains/{brain_id}/sources/{slug}/move', { params: { path: { brain_id: brainId, slug } }, body: { folder } })), onSuccess: () => invalidate(['jobs']) });
}
export function useDeleteSource() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: ({ brainId, slug }: SourceId) => result(api.DELETE('/api/brains/{brain_id}/sources/{slug}', { params: { path: { brain_id: brainId, slug } } })), onSuccess: () => invalidate(['jobs']) });
}
export function useReconvertSource() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: ({ brainId, slug }: SourceId) => result(api.POST('/api/brains/{brain_id}/sources/{slug}/reconvert', { params: { path: { brain_id: brainId, slug } } })), onSuccess: () => invalidate(['jobs']) });
}
export function useIngestSource() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: ({ brainId, slug }: SourceId) => result(api.POST('/api/brains/{brain_id}/sources/{slug}/ingest', { params: { path: { brain_id: brainId, slug } } })), onSuccess: () => invalidate(['jobs']) });
}
