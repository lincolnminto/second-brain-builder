import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { Search } from 'lucide-react';
import { useSearch } from '../api/hooks';
import { t } from '../i18n/en';
import { Dialog } from '../components/ui/dialog';
import { Input } from '../components/ui/input';
import { ErrorNotice, Loading } from '../components/ui/feedback';
import { TypeBadge } from '../components/ui/badge';
import { searchResultUrl, searchUrl } from '../features/search/results';
import { t as searchText } from '../features/search/strings';
import { navigation } from './navigation';

export function CommandPalette({ brainId, open, onOpenChange }: { brainId: string; open: boolean; onOpenChange: (open: boolean) => void }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  const [selection, setSelection] = useState(0);
  useEffect(() => { const timer = setTimeout(() => setDebounced(query.trim()), 200); return () => clearTimeout(timer); }, [query]);
  const results = useSearch(brainId, open ? debounced : '');
  const links = navigation.filter(item => t(item.label).toLowerCase().includes(query.toLowerCase())).map(item => ({ title: t(item.label), path: item.path === '/settings' ? item.path : `/brains/${encodeURIComponent(brainId)}${item.path}`, type: '' }));
  const pages = debounced === query.trim() ? results.data?.map(page => ({ title: page.title, path: searchResultUrl(brainId, page.path), type: page.type })) ?? [] : [];
  const allResults = query.trim() ? [{ title: searchText('allResults', { query: query.trim() }), path: searchUrl(brainId, query), type: '' }] : [];
  const options = [...links, ...pages, ...allResults];
  function go(path: string) { onOpenChange(false); navigate(path); }
  return <Dialog open={open} onOpenChange={onOpenChange} title={t('commandPalette')} description={t('commandHint')} className="command-palette">
    <div className="command-input"><Search size={20} /><Input autoFocus placeholder={t('commandSearch')} aria-label={t('search')} value={query} onChange={e => { setQuery(e.target.value); setSelection(0); }}
      role="combobox" aria-controls="command-results" aria-expanded="true" aria-autocomplete="list" aria-activedescendant={options.length ? `command-option-${Math.min(selection, options.length - 1)}` : undefined}
      onKeyDown={e => {
        if (e.key === 'ArrowDown') { e.preventDefault(); setSelection(index => Math.max(0, Math.min(index + 1, options.length - 1))); }
        if (e.key === 'ArrowUp') { e.preventDefault(); setSelection(index => Math.max(0, index - 1)); }
        if (e.key === 'Enter') { const item = options[Math.max(0, Math.min(selection, options.length - 1))]; if (item) { e.preventDefault(); go(item.path); } }
      }} />
    </div>
    {results.isFetching && <Loading />}<ErrorNotice error={results.error} />
    <div id="command-results" role="listbox" aria-label={t('search')} className="command-results">
      {!!links.length && <p className="muted command-group">{t('jumpTo')}</p>}
      {options.map((item, index) => <div key={item.path}>
        {index === links.length && !!pages.length && <p className="muted command-group">{t('pages')}</p>}
        <button id={`command-option-${index}`} role="option" aria-selected={index === Math.min(selection, options.length - 1)} className="command-option" onClick={() => go(item.path)} onMouseMove={() => setSelection(index)}>
          <span>{item.title}</span>{item.type && <TypeBadge type={item.type} />}
        </button>
      </div>)}
      {!options.length && !results.isFetching && <p className="muted">{t('noResults')}</p>}
    </div>
  </Dialog>;
}
