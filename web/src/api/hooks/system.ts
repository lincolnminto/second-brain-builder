import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, result } from '../client';

export function useAuth() {
  return useQuery({ queryKey: ['auth'], queryFn: () => result(api.GET('/api/auth/status')), staleTime: Infinity });
}
export function useLogin() {
  const cache = useQueryClient();
  return useMutation({ mutationFn: (password: string) => result(api.POST('/api/auth/login', { body: { password } })),
    onSuccess: (data) => { cache.setQueryData(['auth'], data); } });
}
export function useLogout() {
  const cache = useQueryClient();
  return useMutation({ mutationFn: () => result(api.POST('/api/auth/logout')), onSuccess: async () => {
    await cache.cancelQueries();
    cache.removeQueries({ predicate: query => query.queryKey[0] !== 'auth' });
    cache.setQueryData(['auth'], { login_required: true, authenticated: false });
  } });
}
export function useSetup() {
  return useQuery({ queryKey: ['setup'], queryFn: () => result(api.GET('/api/setup')) });
}
export function useHealth() {
  return useQuery({ queryKey: ['health'], queryFn: () => result(api.GET('/api/health')), refetchInterval: 15_000 });
}
