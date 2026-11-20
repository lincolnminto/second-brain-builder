import { useId, useRef, useState } from 'react';
import { BookOpenText, ChevronDown, Folder, X } from 'lucide-react';
import { Link, useSearchParams } from 'react-router';
import { useCurrentBrain } from '../../app/context';
import type { Schema } from '../../api/client';
import { useFolders } from '../../api/hooks';
import { TypeBadge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Loading, QueryError } from '../../components/ui/feedback';
import { t as catalog } from '../../i18n/en';
import { useEscapeToClose, useFloating } from './floating';
import { useWikiPages } from './queries';
import { groupWikiPages, isWikiPageType, WIKI_PAGE_TYPES, type WikiPageType } from './resolution';
import { t } from './strings';

function typeLabel(type: string) {
  return isWikiPageType(type) ? catalog(`pageType_${type}`) : type;
}

// Only known types become a CSS color: a page type is untrusted text.
function TypeDot({ type }: { type: string }) {
  return <span className="wiki-chip-dot" style={{ background: isWikiPageType(type) ? `var(--${type})` : 'var(--muted)' }} aria-hidden />;
}

/** A page type without pages: disabled, with a hint about where such pages come from. */
function EmptyTypeChip({ type }: { type: WikiPageType }) {
  const [open, setOpen] = useState(false);
  const chip = useRef<HTMLButtonElement>(null);
  const hint = useRef<HTMLSpanElement>(null);
  const hintId = useId();
  const style = useFloating(chip, hint, open);
  useEscapeToClose(open, () => setOpen(false));
  return <>
    <button ref={chip} type="button" className="wiki-chip" aria-disabled="true" aria-describedby={hintId}
      onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)} onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}
      onKeyDown={event => { if (event.key === 'Escape' && open) { event.stopPropagation(); setOpen(false); } }}>
      <TypeDot type={type} />{typeLabel(type)} <span className="wiki-chip-count">0</span>
    </button>
    <span ref={hint} id={hintId} role="tooltip" className="wiki-type-hint" hidden={!open} style={style}>{t(`emptyType_${type}`)}</span>
  </>;
}

function folderPaths(node?: Schema['FolderNode']): string[] {
  return (node?.children ?? []).flatMap(child => [child.path, ...folderPaths(child)]);
}

function FolderFilter({ brainId, folder, onChange }: { brainId: string; folder: string; onChange: (folder: string) => void }) {
  const folders = useFolders(brainId);
  const selectId = useId();
  const paths = folderPaths(folders.data);
  if (folder && !paths.includes(folder)) paths.push(folder);
  if (!paths.length) return null;
  return <div className="wiki-chip wiki-folder-chip" data-active={folder ? '' : undefined}>
    <Folder size={14} aria-hidden />
    <label htmlFor={selectId} className="sr-only">{t('folderFilter')}</label>
    <span className="wiki-folder-select">
      <select id={selectId} value={folder} onChange={event => onChange(event.target.value)}>
        <option value="">{t('allFolders')}</option>
        {paths.map(path => <option key={path} value={path}>{path}</option>)}
      </select>
      <ChevronDown size={14} aria-hidden />
    </span>
    {folder && <button type="button" className="wiki-chip-clear" aria-label={t('clearFolder')} onClick={() => onChange('')}><X size={14} aria-hidden /></button>}
  </div>;
}

export function WikiHome() {
  const { brainId } = useCurrentBrain();
  const [params, setParams] = useSearchParams();
  const folder = params.get('folder') ?? '';
  const pages = useWikiPages(brainId, folder);
  const [type, setType] = useState<string | null>(null);
  const [allTypes, setAllTypes] = useState(false);
  const groups = groupWikiPages(pages.data ?? []);
  // A type filter that the Folder filter emptied shows every group again.
  const selected = groups.some(group => group.type === type) ? type : null;
  const empty = WIKI_PAGE_TYPES.filter(known => !groups.some(group => group.type === known));
  const root = `/brains/${encodeURIComponent(brainId)}`;
  function chooseFolder(value: string) {
    setParams(previous => {
      const next = new URLSearchParams(previous);
      if (value) next.set('folder', value); else next.delete('folder');
      return next;
    }, { replace: true });
  }
  return <div className="page wiki-home"><header className="page-heading"><h1>{t('title')}</h1><p className="muted">{t('intro')}</p></header>
    {pages.isPending && <Loading />}{pages.isError && <QueryError error={pages.error} retry={() => void pages.refetch()} />}
    {/* With a Folder filter, the toolbar stays even after an error, so the filter can be cleared. */}
    {(!!folder || (pages.isSuccess && !!groups.length)) && <div className="wiki-toolbar">
      <FolderFilter brainId={brainId} folder={folder} onChange={chooseFolder} />
      {pages.isSuccess && <div className="wiki-type-filter" role="group" aria-label={t('typeFilter')}>
        {groups.map(group => <button key={group.type} type="button" className="wiki-chip" aria-pressed={selected === group.type}
          onClick={() => setType(selected === group.type ? null : group.type)}>
          <TypeDot type={group.type} />{typeLabel(group.type)} <span className="wiki-chip-count">{group.pages.length}</span>
        </button>)}
        {allTypes && empty.map(known => <EmptyTypeChip key={known} type={known} />)}
        {!!empty.length && <button type="button" className="wiki-chip wiki-chip-toggle" aria-pressed={allTypes} onClick={() => setAllTypes(!allTypes)}>{t('allTypes')}</button>}
      </div>}
    </div>}
    {pages.isSuccess && !groups.length && (folder
      ? <div className="wiki-empty"><Folder size={32} strokeWidth={1.2} /><h2>{t('folderEmptyTitle')}</h2><p className="muted">{t('folderEmptyHint')}</p><Button variant="secondary" onClick={() => chooseFolder('')}>{t('allFolders')}</Button></div>
      : <div className="wiki-empty"><BookOpenText size={32} strokeWidth={1.2} /><h2>{t('emptyTitle')}</h2><p className="muted">{t('emptyHint')}</p><Button asChild variant="secondary"><Link to={root}>{t('addSource')}</Link></Button></div>)}
    <div className="wiki-groups" aria-busy={pages.isPlaceholderData || undefined}>
      {groups.filter(group => !selected || group.type === selected).map(group => <section key={group.type} className="wiki-group" aria-label={group.type === 'name-index' ? t('name-index') : typeLabel(group.type)}>
        <header><TypeBadge type={group.type} /><span className="muted">{group.pages.length}</span></header>
        <ul>{group.pages.map(page => <li key={page.path}><Link to={`${root}/wiki/${page.path.split('/').map(encodeURIComponent).join('/')}`}>{page.title}</Link>{page.description && <p className="muted">{page.description}</p>}</li>)}</ul>
      </section>)}
    </div>
  </div>;
}
