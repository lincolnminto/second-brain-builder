import { Search } from 'lucide-react';
import { Link, useSearchParams } from 'react-router';
import { useCurrentBrain } from '../../app/context';
import { useSearch } from '../../api/hooks';
import { SafeMarkdown } from '../../components/markdown';
import { TypeBadge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Field, Input, Select } from '../../components/ui/input';
import { Loading, QueryError } from '../../components/ui/feedback';
import { searchResultUrl, searchTypes } from './results';
import { t } from './strings';
import './search.css';

export function SearchPlaceholder() {
  const { brainId } = useCurrentBrain();
  const [params, setParams] = useSearchParams();
  const query = params.get('q')?.trim() ?? '';
  const type = params.get('type') ?? '';
  const filter = searchTypes.find(value => value === type);
  const results = useSearch(brainId, query, filter ? [filter] : undefined, 50);
  return <div className="page search-page"><header className="page-heading"><h1>{t('title')}</h1><p className="muted">{t('intro')}</p></header>
    <form className="search-form" onSubmit={event => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const next = new URLSearchParams(params);
      const text = String(form.get('q') ?? '').trim();
      if (text) next.set('q', text); else next.delete('q');
      const selected = String(form.get('type') ?? '');
      if (selected) next.set('type', selected); else next.delete('type');
      setParams(next);
    }}>
      <Field label={t('query')}><Input key={query} name="q" type="search" defaultValue={query} placeholder={t('placeholder')} /></Field>
      <Field label={t('filter')}><Select key={filter ?? ''} name="type" defaultValue={filter ?? ''}><option value="">{t('all')}</option>{searchTypes.map(value => <option key={value} value={value}>{t(value)}</option>)}</Select></Field>
      <Button type="submit"><Search size={17} />{t('title')}</Button>
    </form>
    {!query && <div className="search-empty"><Search size={28} strokeWidth={1.2} /><h2>{t('emptyTitle')}</h2><p className="muted">{t('emptyHint')}</p><p className="muted">{t('shortcut')}</p></div>}
    {!!query && <section aria-live="polite" aria-busy={results.isFetching} aria-label={t('results')}>
      {results.isPending && <Loading />}
      {results.isError && <QueryError error={results.error} retry={() => void results.refetch()} />}
      {results.isSuccess && <p className="search-count muted">{t(results.data.length === 50 ? 'limitedCount' : 'count', { count: results.data.length, query })}</p>}
      {results.isSuccess && !results.data.length && <div className="search-empty"><h2>{t('noResults')}</h2><p className="muted">{t('noResultsHint')}</p></div>}
      <ul className="search-results">{results.data?.map(hit => <li key={hit.path}>
        <div className="search-result-heading"><Link to={searchResultUrl(brainId, hit.path)}>{hit.title}</Link><TypeBadge type={hit.type} /></div>
        <p className="mono muted">{hit.path}</p>
        <SafeMarkdown body={hit.snippet} brainId={brainId} pagePath={hit.path} />
      </li>)}</ul>
    </section>}
  </div>;
}
