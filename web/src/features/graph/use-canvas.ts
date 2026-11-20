import { useEffect, useState } from 'react';
import { useTheme } from '../../theme/context';
import { PAGE_TYPES } from './filters';
import type { GraphPalette } from './style';

export function useReducedMotion() {
  const [reduced, setReduced] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReduced(media.matches);
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  return reduced;
}

/** The theme tokens as canvas colors; null until the first read. */
export function useGraphPalette(): GraphPalette | null {
  const { theme } = useTheme();
  const [palette, setPalette] = useState<GraphPalette | null>(null);
  useEffect(() => {
    // ThemeProvider writes the root attribute in its own effect. Read on the
    // next frame, after parent effects, so canvas and CSS switch together.
    const frame = requestAnimationFrame(() => {
      const tokens = getComputedStyle(document.documentElement);
      const read = (name: string) => tokens.getPropertyValue(`--${name}`).trim();
      setPalette({
        dark: theme === 'ink',
        background: read('background'), surface: read('surface'), border: read('border'),
        foreground: read('foreground'), muted: read('muted'),
        types: Object.fromEntries(PAGE_TYPES.map(type => [type, read(type)])),
      });
    });
    return () => cancelAnimationFrame(frame);
  }, [theme]);
  return palette;
}
