import { useCallback, useEffect, useRef, useState } from 'react';

export const NOTICE_MS = 4000;
export type Notice = { text: string; id: number };

/** A short status message that clears itself. Each `show` gets a new id, so a
 * view keyed by it re-announces the same text (for example a second copy). */
export function useNotice(duration = NOTICE_MS): [Notice | null, (text: string) => void] {
  const [notice, setNotice] = useState<Notice | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const count = useRef(0);
  const show = useCallback((text: string) => {
    clearTimeout(timer.current);
    if (!text) {
      setNotice(null);
      return;
    }
    setNotice({ text, id: ++count.current });
    timer.current = setTimeout(() => setNotice(null), duration);
  }, [duration]);
  useEffect(() => () => clearTimeout(timer.current), []);
  return [notice, show];
}
