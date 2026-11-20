import { Link, useParams } from 'react-router';
import { useCurrentBrain } from '../../app/context';
import { usePage } from '../../api/hooks';
import { TypeBadge } from '../../components/ui/badge';
import { Loading, QueryError } from '../../components/ui/feedback';
import { resolveWikiLink } from './resolution';
import { WikiHome } from './wiki-home';
import { WikiMarkdown } from './wiki-markdown';
import { t } from './strings';
import './wiki.css';

export function WikiPlaceholder() {
  const { '*': path = '' } = useParams();
  return path ? <WikiPage path={path} /> : <WikiHome />;
}

function WikiPage({ path }: { path: string }) {
  const { brainId } = useCurrentBrain();
  const page = usePage(brainId, path);
  const root = `/brains/${encodeURIComponent(brainId)}`;
  if (page.isPending) return <Loading />;
  if (page.isError) return <div className="page"><Link to={`${root}/wiki`}>{t('back')}</Link><QueryError error={page.error} retry={() => void page.refetch()} /></div>;
  return <article className="page wiki-page">
    <Link className="wiki-back" to={`${root}/wiki`}>{t('back')}</Link>
    <div className="content-column">
      <header className="page-heading"><div className="wiki-metadata">{page.data.type && <TypeBadge type={page.data.type} />}{page.data.tags.map(tag => <span className="wiki-tag" key={tag}>#{tag}</span>)}</div>
        <h1>{page.data.title}</h1>{page.data.description && <p className="muted">{page.data.description}</p>}
      </header>
      <WikiMarkdown key={`${brainId}:${path}`} brainId={brainId} page={page.data} />
      {!!page.data.sources.length && <footer className="wiki-sources"><h2>{t('sources')}</h2><ul>{page.data.sources.map(source => {
        const link = resolveWikiLink(source.resource, page.data.path, brainId);
        return <li key={source.id}>{link.kind === 'internal' ? <Link to={link.to}>{source.title || source.id}</Link> : <span>{source.title || source.id}</span>}<small className="muted">{t('canonical')}</small></li>;
      })}</ul></footer>}
    </div>
  </article>;
}
