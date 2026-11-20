import {
  memo, useCallback, useEffect, useId, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState,
  type KeyboardEvent, type Ref,
} from 'react';
import { ChevronDown, ChevronUp, ListTree, Search } from 'lucide-react';
import { Virtuoso, type ListRange, type VirtuosoHandle } from 'react-virtuoso';
import { Button } from '../ui/button';
import { Loading, QueryError } from '../ui/feedback';
import { Input } from '../ui/input';
import { findMatches, firstMatchFrom, foldForFilter, sameMatch, type FindMatch } from './find';
import type { HighlightRange } from './highlight';
import { outlineVersion, useDocumentOutline, useSegmentPages, type LoadedSegments, type SegmentPageState } from './queries';
import {
  headingAt, headingTrail, isSmallDocument, leadingTitleLength, localRange, locateSegment, SEGMENT_PAGE_SIZE, segmentPage,
} from './ranges';
import { DEFAULT_ID_PREFIX, SegmentMarkdown } from './segment-markdown';
import { tableHeaders } from './tables';
import { t } from './strings';
import type { DocumentHeading, DocumentOutline, DocumentSegment, DocumentViewerHandle, PassageRange } from './types';
import './document-viewer.css';

const NO_RANGES: readonly HighlightRange[] = [];
const NO_HEADERS: ReadonlyMap<number, string> = new Map();
const PASSAGE_SELECTOR = '[data-dv-passage]';
const CURRENT_SELECTOR = '[data-dv-current]';
const FIND_DELAY_MS = 150;
const TOC_ROW_PX = 34;
const TOC_VISIBLE_ROWS = 10;
/** A rough rendered height per character of Markdown, to size segments before they load. */
const PX_PER_CHAR = 0.42;
/**
 * The reader's scroll box height. Virtuoso puts `height: 100%` on the scroll box
 * itself, which a class cannot override, so the height goes in its style; CSS
 * sets the value for each screen size.
 */
const READER_STYLE = { height: 'var(--dv-reader-height)' } as const;

/** Space above the first segment and below the last one. */
function ListSpacer() {
  return <div className="dv-list-spacer" aria-hidden />;
}
const LIST_COMPONENTS = { Header: ListSpacer, Footer: ListSpacer };

/** An element to scroll to after its segment renders. A new object asks again. */
interface Reveal {
  segment: number;
  selector: string;
}

/**
 * A Markdown page in a virtualized reader: a Canonical Document of several
 * megabytes stays smooth because only the segments near the view are in the DOM.
 *
 * It loads the outline first. A small page (see `isSmallDocument`) renders in one
 * block like `SafeMarkdown`. A longer page gets a table of contents, find in the
 * loaded text, and lazy loading of segments as the reader scrolls.
 *
 * `passage` (or `ref.scrollTo`) scrolls to a range of the body and highlights it.
 * A new `passage` object scrolls again, even with the same offsets.
 */
export function DocumentViewer({ brainId, path, passage = null, ref }: {
  brainId: string;
  path: string;
  passage?: PassageRange | null;
  ref?: Ref<DocumentViewerHandle>;
}) {
  const outline = useDocumentOutline(brainId, path);
  const [target, setTarget] = useState<PassageRange | null>(passage);
  const [shownPassage, setShownPassage] = useState(passage);
  if (passage !== shownPassage) {
    setShownPassage(passage);
    setTarget(passage);
  }
  useImperativeHandle(ref, () => ({
    scrollTo: range => setTarget({ char_start: range.char_start, char_end: range.char_end }),
  }), []);
  if (outline.isPending) return <Loading />;
  if (outline.isError) return <QueryError error={outline.error} retry={() => void outline.refetch()} />;
  // A changed page (another size) starts again: its old loaded pages and positions do not apply.
  const version = outlineVersion(outline.data);
  if (isSmallDocument(outline.data)) return <SmallDocument key={version} brainId={brainId} path={path} outline={outline.data} target={target} />;
  return <LargeDocument key={version} brainId={brainId} path={path} outline={outline.data} target={target} />;
}

function scrollIntoView(element: Element, block: ScrollLogicalPosition) {
  // jsdom has no scrollIntoView.
  if (typeof element.scrollIntoView === 'function') element.scrollIntoView({ block });
}

/** The passage ranges of the loaded segments, as indexes into each segment's Markdown. */
function passageRanges(segments: Iterable<DocumentSegment>, target: PassageRange | null): Map<number, HighlightRange[]> {
  const ranges = new Map<number, HighlightRange[]>();
  if (!target) return ranges;
  for (const segment of segments) {
    const local = localRange(segment, target);
    if (local && local.end > local.start) ranges.set(segment.index, [{ ...local, kind: 'passage' }]);
  }
  return ranges;
}

/** Ranges of a segment's Markdown, for the string `prefix + markdown.slice(strip)` that the viewer renders. */
function shiftRanges(ranges: readonly HighlightRange[], strip: number, prefix: number): readonly HighlightRange[] {
  if (!strip && !prefix) return ranges;
  return ranges
    .filter(range => range.end > strip)
    .map(range => ({ ...range, start: Math.max(0, range.start - strip) + prefix, end: range.end - strip + prefix }));
}

/** The whole body of a small page from its segments, as one segment; null until all are loaded. */
function wholeBody(segments: ReadonlyMap<number, DocumentSegment>, count: number): DocumentSegment | null {
  if (!count || segments.size < count) return null;
  const parts: DocumentSegment[] = [];
  for (let index = 0; index < count; index += 1) {
    const segment = segments.get(index);
    if (!segment) return null;
    parts.push(segment);
  }
  return {
    index: 0,
    char_start: parts[0]?.char_start ?? 0,
    char_end: parts[parts.length - 1]?.char_end ?? 0,
    heading_trail: [],
    markdown: parts.map(segment => segment.markdown).join(''),
  };
}

function SmallDocument({ brainId, path, outline, target }: {
  brainId: string;
  path: string;
  outline: DocumentOutline;
  target: PassageRange | null;
}) {
  const pages = useMemo(() => Array.from({ length: Math.ceil(outline.segments / SEGMENT_PAGE_SIZE) }, (_, page) => page), [outline.segments]);
  const loaded = useSegmentPages(brainId, path, outlineVersion(outline), pages);
  const body = useMemo(() => wholeBody(loaded.segments, outline.segments), [loaded.segments, outline.segments]);
  const strip = body ? leadingTitleLength(body.markdown, outline.title) : 0;
  const ranges = useMemo(
    () => (body ? shiftRanges(passageRanges([body], target).get(0) ?? NO_RANGES, strip, 0) : NO_RANGES),
    [body, target, strip],
  );
  const container = useRef<HTMLDivElement>(null);
  const revealed = useRef<PassageRange | null>(null);
  useEffect(() => {
    if (!target || !body || revealed.current === target) return;
    revealed.current = target;
    const mark = container.current?.querySelector(PASSAGE_SELECTOR);
    if (mark) scrollIntoView(mark, 'center');
  }, [target, body]);
  if (!outline.segments) return <div className="reading" />;
  if (!body) {
    const failed = loaded.pages.find(page => page.status === 'error');
    return failed ? <QueryError error={failed.error} retry={failed.retry} /> : <Loading />;
  }
  return <div className="reading" ref={container}>
    <SegmentMarkdown markdown={strip ? body.markdown.slice(strip) : body.markdown} ranges={ranges} brainId={brainId} pagePath={path} />
  </div>;
}

interface ItemContext {
  loaded: LoadedSegments;
  pages: readonly number[];
  ranges: Map<number, readonly HighlightRange[]>;
  active: FindMatch | null;
  reveal: Reveal | null;
  onReveal: (segment: number, offset: number, reveal: Reveal) => void;
  strip: number;
  /** Table header rows to put before segments that start inside a table. */
  headers: ReadonlyMap<number, string>;
  brainId: string;
  path: string;
  placeholderHeight: number;
}

function renderItem(index: number, _data: unknown, context: ItemContext) {
  const segment = context.loaded.segments.get(index);
  if (!segment) {
    const position = context.pages.indexOf(segmentPage(index));
    return <SegmentPlaceholder index={index} height={context.placeholderHeight} state={context.loaded.pages[position]} />;
  }
  return <SegmentItem
    segment={segment}
    strip={index === 0 ? context.strip : 0}
    prefix={context.headers.get(index) ?? ''}
    ranges={context.ranges.get(index) ?? NO_RANGES}
    current={context.active?.segment === index ? context.active : null}
    reveal={context.reveal?.segment === index ? context.reveal : null}
    onReveal={context.onReveal}
    brainId={context.brainId}
    path={context.path}
  />;
}

function SegmentPlaceholder({ index, height, state }: { index: number; height: number; state: SegmentPageState | undefined }) {
  const failed = state?.status === 'error';
  return <div className="dv-placeholder" style={{ height }} data-dv-segment={index} aria-busy={!failed}>
    {failed ? <>
      <p>{t('loadFailed')}</p>
      <Button variant="secondary" size="small" onClick={state.retry}>{t('retry')}</Button>
    </> : <p>{t('loadingText')}</p>}
  </div>;
}

const SegmentItem = memo(function SegmentItem({ segment, strip, prefix, ranges, current, reveal, onReveal, brainId, path }: {
  segment: DocumentSegment;
  strip: number;
  prefix: string;
  ranges: readonly HighlightRange[];
  current: FindMatch | null;
  reveal: Reveal | null;
  onReveal: (segment: number, offset: number, reveal: Reveal) => void;
  brainId: string;
  path: string;
}) {
  const element = useRef<HTMLDivElement>(null);
  const shown = useMemo(() => {
    const marked = current
      ? ranges.map(range => (range.kind === 'match' && range.start === current.start && range.end === current.end ? { ...range, kind: 'current' as const } : range))
      : ranges;
    return shiftRanges(marked, strip, prefix.length);
  }, [ranges, current, strip, prefix]);
  const markdown = useMemo(() => prefix + (strip ? segment.markdown.slice(strip) : segment.markdown), [prefix, strip, segment.markdown]);
  useLayoutEffect(() => {
    const item = element.current;
    if (!reveal || !item) return;
    const target = item.querySelector(reveal.selector);
    onReveal(segment.index, target ? target.getBoundingClientRect().top - item.getBoundingClientRect().top : 0, reveal);
  }, [reveal, onReveal, segment.index]);
  return <div ref={element} className="dv-segment reading" data-dv-segment={segment.index}>
    <SegmentMarkdown markdown={markdown} ranges={shown} brainId={brainId} pagePath={path}
      segment idPrefix={`${DEFAULT_ID_PREFIX}s${segment.index}-`} />
  </div>;
});

function LargeDocument({ brainId, path, outline, target }: {
  brainId: string;
  path: string;
  outline: DocumentOutline;
  target: PassageRange | null;
}) {
  const ids = useId();
  const tocId = `${ids}-contents`;
  const tocButtonId = `${ids}-contents-button`;
  const findId = `${ids}-find`;
  const findHintId = `${ids}-find-hint`;
  const virtuoso = useRef<VirtuosoHandle>(null);
  const scroller = useRef<HTMLElement | null>(null);
  const frame = useRef<HTMLDivElement>(null);
  const count = outline.segments;
  const [initialIndex] = useState(() => (target ? locateSegment(outline, [], target.char_start)?.index ?? 0 : 0));
  const [pages, setPages] = useState<readonly number[]>(() => [segmentPage(initialIndex)]);
  const want = useCallback((first: number, last: number) => {
    setPages(previous => {
      const added: number[] = [];
      const lastPage = segmentPage(Math.min(count - 1, last));
      for (let page = segmentPage(Math.max(0, first)); page <= lastPage; page += 1) {
        if (!previous.includes(page) && !added.includes(page)) added.push(page);
      }
      return added.length ? [...previous, ...added] : previous;
    });
  }, [count]);
  const loaded = useSegmentPages(brainId, path, outlineVersion(outline), pages);
  const [top, setTop] = useState(initialIndex);
  const [reveal, setReveal] = useState<Reveal | null>(null);
  const [tocOpen, setTocOpen] = useState(false);

  const firstSegment = loaded.segments.get(0);
  const strip = firstSegment ? leadingTitleLength(firstSegment.markdown, outline.title) : 0;
  const headers = useMemo(() => (loaded.segments.size ? tableHeaders(loaded.segments.values()) : NO_HEADERS), [loaded.segments]);

  // Scroll through the list, with the element's offset in its segment: the list
  // scrolls to an index again when sizes change soon after, and keeps the offset.
  const onReveal = useCallback((segment: number, offset: number, done: Reveal) => {
    requestAnimationFrame(() => {
      const view = scroller.current?.clientHeight ?? 0;
      virtuoso.current?.scrollToIndex({ index: segment, align: 'start', offset: Math.max(0, offset - view / 4) });
      setReveal(current => (current === done ? null : current));
    });
  }, []);

  const goTo = useCallback((segment: number, selector: string) => {
    const rendered = scroller.current?.querySelector(`[data-dv-segment="${segment}"]`);
    if (!rendered) virtuoso.current?.scrollToIndex({ index: segment, align: 'start' });
    setReveal({ segment, selector });
  }, []);

  // Open at the passage: find its segment (loading the pages that narrow it down),
  // then scroll to its first highlight.
  const [resolved, setResolved] = useState<PassageRange | null>(null);
  const windowScrolled = useRef<PassageRange | null>(null);
  useEffect(() => {
    if (!target || resolved === target) return;
    const location = locateSegment(outline, loaded.segments.values(), target.char_start);
    if (!location) return;
    const position = pages.indexOf(segmentPage(location.index));
    const status = loaded.pages[position]?.status;
    if (!location.exact && status !== 'success' && status !== 'error') {
      if (position < 0) want(location.index, location.index);
      return;
    }
    setResolved(target);
    if (frame.current && windowScrolled.current !== target) {
      windowScrolled.current = target;
      scrollIntoView(frame.current, 'start');
    }
    virtuoso.current?.scrollToIndex({ index: location.index, align: 'start' });
    setReveal({ segment: location.index, selector: PASSAGE_SELECTOR });
  }, [target, resolved, outline, loaded, pages, want]);

  // Find in the loaded text. The query follows the input after a short pause.
  const [input, setInput] = useState('');
  const [query, setQuery] = useState('');
  const [jump, setJump] = useState(false);
  const [active, setActive] = useState<FindMatch | null>(null);
  useEffect(() => {
    if (input === query) return;
    const timer = window.setTimeout(() => {
      setQuery(input);
      setJump(true);
    }, FIND_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [input, query]);
  const found = useMemo(() => findMatches(loaded.segments.values(), query), [loaded.segments, query]);
  const activeIndex = useMemo(() => found.matches.findIndex(match => sameMatch(match, active)), [found, active]);
  useEffect(() => {
    if (!jump) return;
    setJump(false);
    const first = found.matches[firstMatchFrom(found.matches, top)] ?? null;
    setActive(first);
    if (first) goTo(first.segment, CURRENT_SELECTOR);
  }, [jump, found, top, goTo]);
  const step = (direction: 1 | -1) => {
    const total = found.matches.length;
    if (!total) return;
    const from = activeIndex >= 0 ? activeIndex : firstMatchFrom(found.matches, top) - (direction > 0 ? 1 : 0);
    const next = found.matches[(((from + direction) % total) + total) % total] ?? null;
    setActive(next);
    if (next) goTo(next.segment, CURRENT_SELECTOR);
  };
  const clearFind = () => {
    setInput('');
    setQuery('');
    setActive(null);
  };
  const onFindKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      if (input !== query) {
        setQuery(input);
        setJump(true);
      } else {
        step(event.shiftKey ? -1 : 1);
      }
    } else if (event.key === 'Escape' && input) {
      event.preventDefault();
      event.stopPropagation();
      clearFind();
    }
  };
  const onFrameKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f') {
      event.preventDefault();
      const field = document.getElementById(findId);
      if (field instanceof HTMLInputElement) {
        field.focus();
        field.select();
      }
    }
  };

  const ranges = useMemo(() => {
    const map: Map<number, HighlightRange[]> = passageRanges(loaded.segments.values(), target);
    for (const match of found.matches) {
      const list = map.get(match.segment) ?? [];
      if (!list.length) map.set(match.segment, list);
      list.push({ start: match.start, end: match.end, kind: 'match' });
    }
    return map as Map<number, readonly HighlightRange[]>;
  }, [loaded.segments, target, found]);

  const avgChars = outline.total_chars / Math.max(1, count);
  const placeholderHeight = Math.round(Math.min(6000, Math.max(240, avgChars * PX_PER_CHAR)));
  const context = useMemo<ItemContext>(() => ({
    loaded, pages, ranges, active, reveal, onReveal, strip, headers, brainId, path, placeholderHeight,
  }), [loaded, pages, ranges, active, reveal, onReveal, strip, headers, brainId, path, placeholderHeight]);

  const onRangeChanged = useCallback(({ startIndex, endIndex }: ListRange) => {
    setTop(startIndex);
    want(startIndex - 1, endIndex + 1);
  }, [want]);

  const closeToc = useCallback(() => {
    setTocOpen(false);
    document.getElementById(tocButtonId)?.focus();
  }, [tocButtonId]);
  const dismissToc = useCallback(() => setTocOpen(false), []);
  const jumpToHeading = useCallback((heading: DocumentHeading) => {
    setTocOpen(false);
    virtuoso.current?.scrollToIndex({ index: heading.segment, align: 'start' });
    scroller.current?.focus({ preventScroll: true });
  }, []);

  const trail = loaded.segments.get(top)?.heading_trail ?? headingTrail(outline.headings, top);
  const allLoaded = loaded.segments.size >= count;
  const countText = !query.trim()
    ? ''
    : !found.matches.length
      ? t(allLoaded ? 'findNoneAll' : 'findNone')
      : activeIndex >= 0
        ? t(found.truncated ? 'findCountMore' : 'findCount', { current: activeIndex + 1, total: found.matches.length })
        : t(found.truncated ? 'findTotalMore' : 'findTotal', { total: found.matches.length });
  const missing = target && target.char_start > outline.total_chars;

  return <div className="document-viewer" ref={frame} onKeyDown={onFrameKeyDown}>
    <div className="dv-toolbar">
      <Button id={tocButtonId} variant="ghost" size="small" aria-expanded={tocOpen} aria-controls={tocOpen ? tocId : undefined}
        onClick={() => setTocOpen(open => !open)}>
        <ListTree size={16} aria-hidden />{t('contents')}
      </Button>
      <p className="dv-trail" title={trail.join(' › ')}>{trail.join(' › ')}</p>
      <div className="dv-find" role="search">
        <Search size={16} aria-hidden />
        <Input id={findId} type="search" value={input} placeholder={t('find')} aria-label={t('find')}
          aria-describedby={query.trim() && !allLoaded ? findHintId : undefined}
          onChange={event => setInput(event.target.value)} onKeyDown={onFindKeyDown} />
        <span className="dv-find-count" role="status">{countText}</span>
        <Button className="dv-icon-button" variant="ghost" size="icon" aria-label={t('findPrevious')} disabled={!found.matches.length} onClick={() => step(-1)}>
          <ChevronUp size={16} aria-hidden />
        </Button>
        <Button className="dv-icon-button" variant="ghost" size="icon" aria-label={t('findNext')} disabled={!found.matches.length} onClick={() => step(1)}>
          <ChevronDown size={16} aria-hidden />
        </Button>
      </div>
    </div>
    {query.trim() && !allLoaded && <p id={findHintId} className="dv-find-hint">{t('findHint')}</p>}
    {missing && <p className="dv-notice" role="status">{t('passageMissing')}</p>}
    <div className="dv-body">
      {tocOpen && <TableOfContents id={tocId} toggleId={tocButtonId} headings={outline.headings} top={top}
        onSelect={jumpToHeading} onClose={closeToc} onDismiss={dismissToc} />}
      <Virtuoso<unknown, ItemContext>
        ref={virtuoso}
        className="dv-scroller"
        style={READER_STYLE}
        components={LIST_COMPONENTS}
        role="region"
        aria-label={t('documentText', { title: outline.title })}
        scrollerRef={element => { scroller.current = element instanceof HTMLElement ? element : null; }}
        totalCount={count}
        context={context}
        itemContent={renderItem}
        defaultItemHeight={placeholderHeight}
        initialTopMostItemIndex={initialIndex}
        increaseViewportBy={{ top: 600, bottom: 1200 }}
        rangeChanged={onRangeChanged}
      />
    </div>
  </div>;
}

/**
 * The table of contents popover. Escape closes it and gives focus back to its
 * toggle (`onClose`); a pointer press or focus outside it and its toggle closes
 * it and leaves focus there (`onDismiss`).
 */
function TableOfContents({ id, toggleId, headings, top, onSelect, onClose, onDismiss }: {
  id: string;
  toggleId: string;
  headings: readonly DocumentHeading[];
  top: number;
  onSelect: (heading: DocumentHeading) => void;
  onClose: () => void;
  onDismiss: () => void;
}) {
  const [filter, setFilter] = useState('');
  const shown = useMemo(() => {
    const wanted = foldForFilter(filter.trim());
    return wanted ? headings.filter(heading => foldForFilter(heading.text).includes(wanted)) : headings;
  }, [headings, filter]);
  const nav = useRef<HTMLElement>(null);
  const outside = useCallback((target: EventTarget | null) => (
    target instanceof Node && !nav.current?.contains(target) && !document.getElementById(toggleId)?.contains(target)
  ), [toggleId]);
  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (outside(event.target)) onDismiss();
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [outside, onDismiss]);
  const minLevel = useMemo(() => headings.reduce((level, heading) => Math.min(level, heading.level), 6), [headings]);
  const current = headings[headingAt(headings, top)];
  const [initialIndex] = useState(() => Math.max(0, (current ? headings.indexOf(current) : 0) - 2));
  return <nav id={id} ref={nav} className="dv-toc" aria-label={t('contents')}
    onBlur={event => {
      // No related target: focus went nowhere (a click on a part that takes no focus).
      if (event.relatedTarget && outside(event.relatedTarget)) onDismiss();
    }}
    onKeyDown={event => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
      }
    }}>
    <Input type="search" autoFocus value={filter} placeholder={t('filterHeadings')} aria-label={t('filterHeadings')}
      onChange={event => setFilter(event.target.value)} />
    {!headings.length ? <p className="dv-toc-empty muted">{t('noHeadings')}</p>
      : !shown.length ? <p className="dv-toc-empty muted">{t('noHeadingMatches')}</p>
        : <Virtuoso
          style={{ height: Math.min(shown.length, TOC_VISIBLE_ROWS) * TOC_ROW_PX }}
          tabIndex={-1}
          totalCount={shown.length}
          fixedItemHeight={TOC_ROW_PX}
          initialTopMostItemIndex={filter ? 0 : initialIndex}
          itemContent={index => {
            const heading = shown[index];
            if (!heading) return null;
            return <button type="button" className="dv-toc-item" title={heading.text}
              style={{ paddingLeft: 8 + (heading.level - minLevel) * 14 }}
              aria-current={heading === current ? 'location' : undefined}
              onClick={() => onSelect(heading)}>{heading.text}</button>;
          }}
        />}
  </nav>;
}
