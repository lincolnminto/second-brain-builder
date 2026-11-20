import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type RefObject } from 'react';

const GAP = 8;
const EDGE = 16;

/** The fixed position of a floating element next to its anchor: below it when it
 * fits, else above it. It follows the anchor on scroll and resize while open.
 * `content` is anything that changes the size of the floating element. */
export function useFloating(anchor: RefObject<HTMLElement | null>, floating: RefObject<HTMLElement | null>, open: boolean, content?: unknown): CSSProperties {
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const target = anchor.current?.getBoundingClientRect();
      const element = floating.current;
      if (!target || !element) return;
      const left = Math.max(EDGE, Math.min(target.left, window.innerWidth - element.offsetWidth - EDGE));
      const below = target.bottom + GAP;
      const above = target.top - GAP - element.offsetHeight;
      const top = below + element.offsetHeight <= window.innerHeight - EDGE || above < EDGE ? below : above;
      setPosition(previous => previous?.left === left && previous.top === top ? previous : { left, top });
    };
    place();
    window.addEventListener('scroll', place, true);
    window.addEventListener('resize', place);
    return () => {
      window.removeEventListener('scroll', place, true);
      window.removeEventListener('resize', place);
    };
  }, [anchor, floating, open, content]);
  return position ?? { left: 0, top: 0, visibility: 'hidden' };
}

/** While `open`, Escape anywhere on the page calls `close`. Content that hover opens
 * must close without a move of the pointer or the focus (WCAG 1.4.13), and focus can
 * be outside the element that opened it. */
export function useEscapeToClose(open: boolean, close: () => void) {
  const latest = useRef(close);
  useLayoutEffect(() => { latest.current = close; });
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') latest.current(); };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [open]);
}
