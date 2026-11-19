import { useMutation, useQuery } from '@tanstack/react-query';
import { api, result, type Schema } from '../client';
import { useInvalidate } from './shared';

export function useBrains() {
  return useQuery({ queryKey: ['brains'], queryFn: () => result(api.GET('/api/brains')), refetchInterval: (q) => q.state.data?.some(b => b.status === 'creating') ? 3000 : false });
}
export function useBrain(brainId: string) {
  return useQuery({ queryKey: ['brain', brainId], queryFn: () => result(api.GET('/api/brains/{brain_id}', { params: { path: { brain_id: brainId } } })), enabled: !!brainId });
}
export function useCreateBrain() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: (body: Schema['BrainCreate']) => result(api.POST('/api/brains', { body })),
    onSuccess: () => invalidate(['brains'], ['setup'], ['jobs'], ['github', 'repos'], ['agent-setup']) });
}
export function useDisconnectBrain() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: (brain_id: string) => result(api.DELETE('/api/brains/{brain_id}', { params: { path: { brain_id } } })),
    onSuccess: () => invalidate(['brains'], ['setup'], ['agent-setup']) });
}
export function useSyncBrain() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: (brain_id: string) => result(api.POST('/api/brains/{brain_id}/sync', { params: { path: { brain_id } } })), onSuccess: () => invalidate(['jobs']) });
}
export function useLfsUsage(brainId: string) {
  return useQuery({ queryKey: ['lfs-usage', brainId], queryFn: () => result(api.GET('/api/brains/{brain_id}/lfs-usage', { params: { path: { brain_id: brainId } } })), enabled: !!brainId });
}
