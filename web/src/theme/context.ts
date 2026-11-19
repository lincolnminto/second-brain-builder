import { createContext, useContext } from 'react';
export type Theme = 'paper' | 'ink';
export const ThemeContext = createContext<{ theme: Theme; setTheme: (theme: Theme) => void }>({ theme: 'paper', setTheme: () => undefined });
export function useTheme() { return useContext(ThemeContext); }
