import { useMutation, useQuery } from '@tanstack/react-query';
import { api, result } from '../client';
import { useInvalidate } from './shared';

export function useGitHubAccount() {
  return useQuery({ queryKey: ['github', 'account'], queryFn: () => result(api.GET('/api/github/account')) });
}
export function useGitHubRepos(enabled = true) {
  return useQuery({ queryKey: ['github', 'repos'], queryFn: () => result(api.GET('/api/github/repos')), enabled });
}
export function useGitHubInstallUrl(enabled = true) {
  return useQuery({ queryKey: ['github', 'install-url'], queryFn: () => result(api.GET('/api/github/install-url')), enabled });
}
export function useStartDeviceFlow() {
  return useMutation({ mutationFn: () => result(api.POST('/api/github/device/start')) });
}
export function usePollDeviceFlow() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: () => result(api.POST('/api/github/device/poll')), onSuccess: (data) => {
    if (data.status === 'connected') void invalidate(['github'], ['setup']);
  } });
}
export function useConnectPat() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: (token: string) => result(api.POST('/api/github/pat', { body: { token } })),
    onSuccess: () => invalidate(['github'], ['setup']) });
}
export function useDisconnectGitHub() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: () => result(api.DELETE('/api/github/account')), onSuccess: () => invalidate(['github'], ['setup']) });
}
