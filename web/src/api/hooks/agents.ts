import { useMutation, useQuery } from '@tanstack/react-query';
import { api, result } from '../client';
import { useInvalidate } from './shared';

export function useTokens() {
  return useQuery({ queryKey: ['tokens'], queryFn: () => result(api.GET('/api/tokens')) });
}
export function useCreateToken() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: (name: string) => result(api.POST('/api/tokens', { body: { name } })), onSuccess: () => invalidate(['tokens']) });
}
export function useDeleteToken() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: (token_id: number) => result(api.DELETE('/api/tokens/{token_id}', { params: { path: { token_id } } })), onSuccess: () => invalidate(['tokens']) });
}
export function useAgentSetup() {
  return useQuery({ queryKey: ['agent-setup'], queryFn: () => result(api.GET('/api/agent-setup')) });
}
