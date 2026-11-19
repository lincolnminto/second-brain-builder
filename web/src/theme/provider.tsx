import { useEffect, useState, type ReactNode } from 'react';
import { ThemeContext, type Theme } from './context';
function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem('sbb-theme');
    if (saved === 'paper' || saved === 'ink') return saved;
  } catch { /* Storage may be disabled. */ }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'ink' : 'paper';
}
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme === 'ink' ? 'dark' : 'light';
    try { localStorage.setItem('sbb-theme', theme); } catch { /* Keep the current session theme. */ }
  }, [theme]);
  return <ThemeContext.Provider value={{ theme, setTheme }}>{children}</ThemeContext.Provider>;
}
