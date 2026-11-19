import { useCallback } from 'react';
import { useQueryClient, type QueryKey } from '@tanstack/react-query';

export function useInvalidate() {
  const cache = useQueryClient();
  return useCallback((...keys: QueryKey[]) => Promise.all(keys.map((queryKey) => cache.invalidateQueries({ queryKey }))), [cache]);
}
