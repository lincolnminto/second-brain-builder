import { BookOpenText } from 'lucide-react';
import { Link, useParams } from 'react-router';
import { useCurrentBrain } from '../../app/context';
import { usePage, usePages } from '../../api/hooks';
import { TypeBadge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Loading, QueryError } from '../../components/ui/feedback';
import { groupWikiPages, resolveWikiLink } from './resolution';
import { WikiMarkdown } from './wiki-markdown';
import { t } from './strings';
import './wiki.css';

export function WikiPlaceholder() {
  const { '*': path = '' } = useParams();
  return path ? <WikiPage path={path} /> : <WikiHome />;
}

function WikiHome() {
  const { brainId } = useCurrentBrain();
  const pages = usePages(brainId);
  const groups = groupWikiPages(pages.data ?? []);
  const root = `/brains/${encodeURIComponent(brainId)}`;
  return <div className="page wiki-home"><header className="page-heading"><h1>{t('title')}</h1><p className="muted">{t('intro')}</p></header>
    {pages.isPending && <Loading />}{pages.isError && <QueryError error={pages.error} retry={() => void pages.refetch()} />}
    {pages.isSuccess && !groups.length && <div className="wiki-empty"><BookOpenText size={32} strokeWidth={1.2} /><h2>{t('emptyTitle')}</h2><p className="muted">{t('emptyHint')}</p><Button asChild variant="secondary"><Link to={root}>{t('addSource')}</Link></Button></div>}
    {groups.map(group => <section key={group.type} className="wiki-group" aria-label={group.type}>
      <header><TypeBadge type={group.type} /><span className="muted">{group.pages.length}</span></header>
      <ul>{group.pages.map(page => <li key={page.path}><Link to={`${root}/wiki/${page.path.split('/').map(encodeURIComponent).join('/')}`}>{page.title}</Link>{page.description && <p className="muted">{page.description}</p>}</li>)}</ul>
    </section>)}
  </div>;
}

function WikiPage({ path }: { path: string }) {
  const { brainId } = useCurrentBrain();
  const page = usePage(brainId, path);
  const root = `/brains/${encodeURIComponent(brainId)}`;
  if (page.isPending) return <Loading />;
  if (page.isError) return <div className="page"><Link to={`${root}/wiki`}>{t('back')}</Link><QueryError error={page.error} retry={() => void page.refetch()} /></div>;
  return <article className="page wiki-page">
    <Link className="wiki-back" to={`${root}/wiki`}>{t('back')}</Link>
    <header className="page-heading"><div className="wiki-metadata">{page.data.type && <TypeBadge type={page.data.type} />}{page.data.tags.map(tag => <span className="wiki-tag" key={tag}>#{tag}</span>)}</div>
      <h1>{page.data.title}</h1>{page.data.description && <p className="muted">{page.data.description}</p>}
    </header>
    <WikiMarkdown key={`${brainId}:${path}`} brainId={brainId} page={page.data} />
    {!!page.data.sources.length && <footer className="wiki-sources"><h2>{t('sources')}</h2><ul>{page.data.sources.map(source => {
      const link = resolveWikiLink(source.resource, page.data.path, brainId);
      return <li key={source.id}>{link.kind === 'internal' ? <Link to={link.to}>{source.title || source.id}</Link> : <span>{source.title || source.id}</span>}<small className="muted">{t('canonical')}</small></li>;
    })}</ul></footer>}
  </article>;
}
