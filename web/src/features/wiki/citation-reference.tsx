import { Fragment, useEffect, useId, useMemo, useRef, useState, type FocusEvent, type KeyboardEvent, type MouseEvent, type ReactNode } from 'react';
import { ArrowUpRight, LoaderCircle } from 'lucide-react';
import { Link, useNavigate } from 'react-router';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, type Schema } from '../../api/client';
import { highlightTerms, keyTerms } from './citations';
import { useEscapeToClose, useFloating } from './floating';
import { citationQuery } from './queries';
import { passageUrl } from './resolution';
import { t } from './strings';

const OPEN_DELAY = 120;
const CLOSE_DELAY = 160;

type Props = {
  brainId: string; pagePath: string;
  /** The number of the footnote reference in the rendered page, from 0. */
  refIndex: number;
  /** The plain text of the sentence that holds the reference: its words are the match terms. */
  sentence: string;
  /** The id of the cited Source, from the footnote label. */
  sourceId: string;
  title: string;
  /** The cited Canonical Document, for when no passage is known. */
  documentTo: string;
  id?: string; children: ReactNode;
};

function isPlainClick(event: MouseEvent) {
  return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
}

/** The passage, when it is from the cited Source. A passage of another Source means
 * that this page and the server count references in another way: it is not shown. */
function citedPassage(passage: Schema['CitationPassage'] | undefined, sourceId: string) {
  return passage && passage.source_id.toLowerCase() === sourceId.toLowerCase() ? passage : undefined;
}

/** A footnote reference that shows the cited passage on hover or focus, and opens the
 * Source view at that passage. Passage text is untrusted: it renders as text only. */
export function CitationReference({ brainId, pagePath, refIndex, sentence, sourceId, title, documentTo, id, children }: Props) {
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLAnchorElement>(null);
  const card = useRef<HTMLSpanElement>(null);
  const openLink = useRef<HTMLAnchorElement>(null);
  const timer = useRef<number | undefined>(undefined);
  const hovered = useRef(false);
  const focused = useRef(false);
  const refocusing = useRef(false);
  const mounted = useRef(true);
  const bodyId = useId();
  const client = useQueryClient();
  const navigate = useNavigate();
  const passage = useQuery({ ...citationQuery(brainId, pagePath, refIndex), enabled: open });
  const style = useFloating(anchor, card, open, passage.status);
  const terms = useMemo(() => keyTerms(sentence), [sentence]);
  const found = citedPassage(passage.data, sourceId);
  const parts = useMemo(() => found ? highlightTerms(found.text, terms) : [], [found, terms]);
  const target = (found && passageUrl(brainId, found)) || documentTo;
  const notFound = (passage.error instanceof ApiError && passage.error.status === 404) || (!!passage.data && !found);
  const trail = found?.heading_trail.filter((heading, index) => index > 0 || heading !== found.source_title) ?? [];

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; window.clearTimeout(timer.current); };
  }, []);
  function schedule(next: boolean, delay: number) {
    window.clearTimeout(timer.current);
    if (next === open) return;
    timer.current = window.setTimeout(() => setOpen(next), delay);
  }
  function show() { window.clearTimeout(timer.current); setOpen(true); }
  function hide() { window.clearTimeout(timer.current); setOpen(false); }
  useEscapeToClose(open, hide);

  // Without a passage yet, a plain click waits for it, so a tap without hover also lands on the passage.
  function follow(event: MouseEvent<HTMLAnchorElement>) {
    if (passage.data || passage.isError || !isPlainClick(event)) return;
    event.preventDefault();
    void client.fetchQuery(citationQuery(brainId, pagePath, refIndex)).then(
      data => { const cited = citedPassage(data, sourceId); return (cited && passageUrl(brainId, cited)) ?? documentTo; },
      () => documentTo,
    ).then(to => { if (mounted.current) void navigate(to); });
  }
  function onFocus() {
    focused.current = true;
    if (refocusing.current) { refocusing.current = false; return; }
    show();
  }
  function onBlur(event: FocusEvent<HTMLSpanElement>) {
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
    focused.current = false;
    if (!hovered.current) hide();
  }
  // The retry button goes away when the passage loads: its focus moves to the link of
  // the card, so it does not drop to the page and Escape still closes the card.
  function retry(event: MouseEvent<HTMLButtonElement>) {
    const button = event.currentTarget;
    const hadFocus = document.activeElement === button;
    void passage.refetch().then(next => {
      const active = document.activeElement;
      if (hadFocus && next.isSuccess && (active === button || active === document.body || active === null)) openLink.current?.focus();
    });
  }
  function onKeyDown(event: KeyboardEvent<HTMLSpanElement>) {
    if (event.key !== 'Escape' || !open) return;
    event.preventDefault();
    event.stopPropagation();
    hide();
    if (card.current?.contains(document.activeElement)) {
      refocusing.current = true;
      anchor.current?.focus();
    }
  }

  return <span className="wiki-citation-anchor" onFocus={onFocus} onBlur={onBlur} onKeyDown={onKeyDown}
    onMouseEnter={() => { hovered.current = true; schedule(true, OPEN_DELAY); }}
    onMouseLeave={() => { hovered.current = false; if (!focused.current) schedule(false, CLOSE_DELAY); }}>
    <Link ref={anchor} to={target} id={id} className="wiki-citation" onClick={follow}
      aria-label={t('citation', { title })} aria-describedby={open ? bodyId : undefined}>{children}</Link>
    {open && <span ref={card} className="wiki-citation-card" style={style}>
      <span className="wiki-citation-source">
        <strong>{found?.source_title || title}</strong>
        {!!trail.length && <span className="wiki-citation-trail">{trail.join(t('trailSeparator'))}</span>}
      </span>
      <span id={bodyId} className="wiki-citation-body" aria-live="polite">
        {passage.isPending && <span className="wiki-citation-status"><LoaderCircle className="spin" size={14} aria-hidden />{t('passageLoading')}</span>}
        {found && <span className="wiki-citation-passage">
          {parts.map((part, index) => part.match ? <mark key={index}>{part.text}</mark> : <Fragment key={index}>{part.text}</Fragment>)}
        </span>}
        {(passage.isError || notFound) && <span className="wiki-citation-status">{notFound ? t('passageNotFound') : t('passageError')}</span>}
      </span>
      <span className="wiki-citation-actions">
        {passage.isError && !notFound && <button type="button" className="wiki-citation-retry" onClick={retry}>{t('retry')}</button>}
        <Link ref={openLink} to={target} className="wiki-citation-open" onClick={follow}>{t('openInDocument')}<ArrowUpRight size={14} aria-hidden /></Link>
      </span>
    </span>}
  </span>;
}
