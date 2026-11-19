import { BookOpenText, House, Network, Search, Settings2 } from 'lucide-react';
import type { MessageKey } from '../i18n/en';
export const navigation = [
  { label: 'home', path: '', icon: House }, { label: 'wiki', path: '/wiki', icon: BookOpenText },
  { label: 'graph', path: '/graph', icon: Network }, { label: 'search', path: '/search', icon: Search },
  { label: 'settings', path: '/settings', icon: Settings2 },
] satisfies { label: MessageKey; path: string; icon: typeof House }[];
