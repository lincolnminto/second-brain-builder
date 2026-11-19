import { useEffect, useRef, useState } from 'react';
import { useTheme } from '../../theme/context';
import { strings } from './strings';

export function useCanvasSize() {
  const container = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const measure = () => {
      const { width, height } = element.getBoundingClientRect();
      setSize(previous => previous.width === width && previous.height === height ? previous : { width, height });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return { container, ...size };
}

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

export function useGraphPalette() {
  const { theme } = useTheme();
  const [palette, setPalette] = useState<Record<string, string>>({});
  useEffect(() => {
    // ThemeProvider writes the root attribute in its own effect. Read on the
    // next frame, after parent effects, so canvas and CSS switch together.
    const frame = requestAnimationFrame(() => {
      const tokens = getComputedStyle(document.documentElement);
      setPalette(Object.fromEntries(['background', 'surface', 'border', 'foreground', 'muted', 'accent', ...Object.keys(strings.pageTypes)]
        .map(name => [name, tokens.getPropertyValue(`--${name}`).trim()])));
    });
    return () => cancelAnimationFrame(frame);
  }, [theme]);
  return palette;
}
