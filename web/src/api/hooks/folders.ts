import { useMutation } from '@tanstack/react-query';
import { api, result } from '../client';
import { useInvalidate } from './shared';

// Each change is a job: the tree refreshes from its job events (see `useJobEvents`).
type FolderInput = { brainId: string; folder: string };
export function useCreateFolder() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: ({ brainId, folder }: FolderInput) => result(api.POST('/api/brains/{brain_id}/folders', { params: { path: { brain_id: brainId } }, body: { folder } })), onSuccess: () => invalidate(['jobs']) });
}
export function useRenameFolder() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: ({ brainId, folder, newFolder }: FolderInput & { newFolder: string }) => result(api.POST('/api/brains/{brain_id}/folders/rename', { params: { path: { brain_id: brainId } }, body: { folder, new_folder: newFolder } })), onSuccess: () => invalidate(['jobs']) });
}
export function useDeleteFolder() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: ({ brainId, folder }: FolderInput) => result(api.DELETE('/api/brains/{brain_id}/folders', { params: { path: { brain_id: brainId }, query: { folder } } })), onSuccess: () => invalidate(['jobs']) });
}
