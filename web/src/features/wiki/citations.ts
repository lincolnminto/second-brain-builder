// Footnote references of a rendered Wiki Page, numbered as the citations API counts
// them (GET /citations/resolve?ref=n): the references of the body first, then those
// in footnote definitions. remark-rehype appends the footnote section after the body,
// so the order of the references in the rendered tree is that order.

type HastNode = {
  type: string; tagName?: string; value?: string;
  properties?: Record<string, unknown>; children?: HastNode[];
};

// Elements that hold their own sentences. A reference belongs to the nearest one.
const BLOCK_TAGS = new Set([
  'address', 'article', 'aside', 'blockquote', 'caption', 'dd', 'details', 'div', 'dl', 'dt', 'figcaption', 'figure',
  'footer', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'header', 'hr', 'li', 'main', 'nav', 'ol', 'p', 'pre', 'section',
  'summary', 'table', 'tbody', 'td', 'tfoot', 'th', 'thead', 'tr', 'ul',
]);

function isElement(node: HastNode, tagName?: string) {
  return node.type === 'element' && (tagName === undefined || node.tagName === tagName);
}
function isFootnoteReference(node: HastNode) {
  return isElement(node, 'a') && !!node.properties?.dataFootnoteRef;
}
function isBackReference(node: HastNode) {
  return isElement(node, 'a') && node.properties?.dataFootnoteBackref !== undefined;
}
function isBlock(node: HastNode) {
  return isElement(node) && BLOCK_TAGS.has(node.tagName ?? '');
}

/** A rehype plugin: each footnote reference gets `dataCitationRef` (its number from 0)
 * and `dataCitedSentence` (the plain text of the sentence that holds it). */
export function citationRefs() {
  return (tree: HastNode) => {
    let count = 0;
    const blocks = new Map<HastNode, HastNode[]>();
    const visit = (node: HastNode, block: HastNode) => {
      if (isFootnoteReference(node)) {
        node.properties = { ...node.properties, dataCitationRef: count++ };
        const references = blocks.get(block);
        if (references) references.push(node); else blocks.set(block, [node]);
        return;
      }
      const inner = isBlock(node) ? node : block;
      for (const child of node.children ?? []) visit(child, inner);
    };
    visit(tree, tree);
    for (const [block, references] of blocks) {
      const { text, offsets } = blockText(block);
      const sentences = sentencesAt(text, offsets);
      references.forEach((reference, index) => {
        reference.properties = { ...reference.properties, dataCitedSentence: sentences[index] ?? '' };
      });
    }
  };
}

/** The plain text of a block without its nested blocks and footnote marks, and the
 * offset in that text of each footnote reference, in order. */
function blockText(block: HastNode) {
  let text = '';
  const offsets: number[] = [];
  const walk = (node: HastNode) => {
    if (node.type === 'text') { text += node.value ?? ''; return; }
    if (node.type !== 'element' && node.type !== 'root') return;
    if (node !== block && isBlock(node)) { text += ' '; return; }
    if (isFootnoteReference(node)) { offsets.push(text.length); return; }
    if (isBackReference(node)) return;
    if (isElement(node, 'br')) { text += ' '; return; }
    if (isElement(node, 'img')) { text += String(node.properties?.alt ?? ''); return; }
    node.children?.forEach(walk);
  };
  walk(block);
  return { text, offsets };
}

// search.cited_sentence on the server marks the reference with a NUL in the text of
// its paragraph, and a sentence end can take that mark. This code does the same, so
// both sides take the same sentence: "fish. [^a] It" gives "It …", and "fish.[^a]" It"
// does not end a sentence. Remark turns a NUL of the page into U+FFFD, so the mark is
// the only one in the text.
const MARK = '\u0000';
// The end of a sentence: end punctuation, closing quotes or brackets, the mark right
// after them, then whitespace or the end of the text. The server's regex, unchanged.
// eslint-disable-next-line no-control-regex -- the NUL is the reference mark, as on the server.
const SENTENCE_END = /(?<![.!?…])[.!?…]+["'”’»)\]]*\u0000?(?:\s+|$)/gu;

/** For each offset in `text`, the sentence that holds it, on one line, as
 * search.cited_sentence takes it.
 *
 * A sentence ends at `.`, `!`, `?`, or `…` followed by whitespace, outside
 * parentheses. A reference right after the end of a sentence belongs to that
 * sentence ("…fish.[^a] It…"); a reference after whitespace belongs to the next one. */
export function sentencesAt(text: string, offsets: readonly number[]): string[] {
  return offsets.map(offset => markedSentence(text.slice(0, offset) + MARK + text.slice(offset)));
}

/** The sentence of `text` that holds the mark, on one line and without the mark. */
function markedSentence(text: string) {
  const depths = new Int32Array(text.length);
  let depth = 0;
  for (let index = 0; index < text.length; index++) {
    depths[index] = depth;
    if (text[index] === '(') depth++;
    else if (text[index] === ')' && depth > 0) depth--;
  }
  const mark = text.indexOf(MARK);
  let start = 0;
  let end = text.length;
  for (const match of text.matchAll(SENTENCE_END)) {
    if ((depths[match.index] ?? 0) > 0) continue;
    const matchEnd = match.index + match[0].length;
    if (matchEnd <= mark) start = matchEnd;
    else { end = matchEnd; break; }
  }
  return text.slice(start, end).replace(MARK, '').replace(/\s+/gu, ' ').trim();
}

const WORD = /[\p{L}\p{N}\p{M}]+/gu;
const NUMBER = /^\p{N}+$/u;
// Folded common words that carry no topic, as in search.key_terms on the server.
const STOPWORDS = new Set((
  'about after again also and any are because been before being both but can compare could did does each for from had ' +
  'has have her hers him his how into its may more most not now only other our out over see she should some such than ' +
  'that the their them then there these they this those through too under very was were what when where which while who ' +
  'whom why will with would you your aos aquela aquele aquilo as com como das dela dele depois dos ela elas ele eles ' +
  'entre essa esse esta este foi isso isto mais mas mesmo muito nao nas nem nos num numa onde para pela pelas pelo pelos ' +
  'por porque qual quando que quem sao seu seus sem sob sobre sua suas tambem tem uma umas uns veja ver vide del las los ' +
  'pero segun son unos cf cfr ibid idem am an at by da de do du el em im in la le na no of on or ou to'
).split(' '));

/** Lowercase, without diacritics: how the passage index compares words. */
export function fold(word: string) {
  return word.toLowerCase().normalize('NFKD').replace(/\p{M}/gu, '');
}

/** The distinct topic words of a sentence, lowercase, in order: words of at least
 * three characters that are not numbers or common words. */
export function keyTerms(text: string): string[] {
  const words = [...text.normalize('NFC').toLowerCase().matchAll(WORD)].map(match => match[0]);
  let terms = words.filter(word => [...word].length >= 3 && !NUMBER.test(word) && !STOPWORDS.has(fold(word)));
  if (!terms.length) terms = words.filter(word => [...word].length >= 2 && !NUMBER.test(word));
  return [...new Set(terms)];
}

export type TextPart = { text: string; match: boolean };

/** `text` cut into parts, with `match` for each whole word that is one of `terms`
 * without case and diacritics. Joined together, the parts give `text` again. */
export function highlightTerms(text: string, terms: readonly string[]): TextPart[] {
  const wanted = new Set(terms.map(fold));
  const parts: TextPart[] = [];
  let last = 0;
  if (wanted.size) {
    for (const match of text.matchAll(WORD)) {
      if (!wanted.has(fold(match[0]))) continue;
      if (match.index > last) parts.push({ text: text.slice(last, match.index), match: false });
      parts.push({ text: match[0], match: true });
      last = match.index + match[0].length;
    }
  }
  if (last < text.length) parts.push({ text: text.slice(last), match: false });
  return parts;
}
