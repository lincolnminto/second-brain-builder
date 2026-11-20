import type { Schema } from '../../api/client';

/**
 * A range of a page body: `body[char_start:char_end]`.
 *
 * Offsets count Unicode code points after the frontmatter, as the documents and
 * citations routes do. A `CitationPassage` is a valid `PassageRange`.
 */
export interface PassageRange {
  char_start: number;
  char_end: number;
}

export type DocumentOutline = Schema['DocumentOutline'];
export type DocumentHeading = Schema['DocumentHeading'];
export type DocumentSegment = Schema['DocumentSegment'];

/** What a viewer can scroll to from outside: `DocumentViewer`'s `ref`. */
export interface DocumentViewerHandle {
  /** Scroll to a range of the body and highlight it. */
  scrollTo(range: PassageRange): void;
}
