import { useMutation, useQuery } from '@tanstack/react-query';
import { api, result, type Schema } from '../client';
import { useInvalidate } from './shared';

export function useProviders() {
  return useQuery({ queryKey: ['providers'], queryFn: () => result(api.GET('/api/providers')) });
}
export function useCreateProvider() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: (body: Schema['ProviderInput']) => result(api.POST('/api/providers', { body })), onSuccess: () => invalidate(['providers'], ['setup']) });
}
export function useUpdateProvider() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: ({ id, body }: { id: number; body: Schema['ProviderUpdate'] }) => result(api.PATCH('/api/providers/{provider_id}', { params: { path: { provider_id: id } }, body })), onSuccess: () => invalidate(['providers'], ['models']) });
}
export function useDeleteProvider() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: (provider_id: number) => result(api.DELETE('/api/providers/{provider_id}', { params: { path: { provider_id } } })), onSuccess: () => invalidate(['providers'], ['tasks'], ['setup']) });
}
export function useTestProvider() {
  return useMutation({ mutationFn: (provider_id: number) => result(api.POST('/api/providers/{provider_id}/test', { params: { path: { provider_id } } })) });
}
export function useProviderModels(providerId?: number) {
  return useQuery({ queryKey: ['models', providerId], queryFn: () => result(api.GET('/api/providers/{provider_id}/models', { params: { path: { provider_id: providerId! } } })), enabled: providerId !== undefined });
}
export function useTaskSettings() {
  return useQuery({ queryKey: ['tasks'], queryFn: () => result(api.GET('/api/settings/tasks')) });
}
export function useSaveTaskSettings() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: (body: Schema['TaskSettingsInput']) => result(api.PUT('/api/settings/tasks', { body })), onSuccess: () => invalidate(['tasks'], ['setup']) });
}
