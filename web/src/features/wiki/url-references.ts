// remark-gfm links a bare URL up to the next space, so in "https://example.com/a.[^a]"
// the link takes "[^a" and the footnote reference is lost. The server reads Markdown
// without bare-URL links (markdown-it without linkify), so it counts a reference there,
// and the citations API numbers references that way (GET /citations/resolve?ref=n).
// This remark plugin cuts such a link at each reference to a defined footnote, so the
// page shows the reference and both sides count the same references.

type MdastNode = {
  type: string; value?: string; url?: string; title?: string | null;
  identifier?: string; label?: string; children?: MdastNode[];
  position?: { start: { offset?: number }; end: { offset?: number } };
};

// A footnote call: "[^label]", or "[^label" at the end of the link text when the
// text after the link starts with "]". A label has no whitespace or brackets.
const CALL = /\[\^([^\s[\]]+)(\]|$)/gu;
// GFM leaves these out of the end of a bare URL.
const TRAILING_PUNCTUATION = new Set(['?', '!', '.', ',', ':', '*', '_', '~']);

/** A label as mdast stores it in `identifier`: whitespace collapsed, case folded. */
function identifierOf(label: string) {
  return label.replace(/[\t\n\r ]+/gu, ' ').trim().toLowerCase().toUpperCase().toLowerCase();
}

function definedFootnotes(node: MdastNode, found = new Set<string>()) {
  if (node.type === 'footnoteDefinition' && node.identifier) found.add(node.identifier);
  for (const child of node.children ?? []) definedFootnotes(child, found);
  return found;
}

/** The text of a bare-URL link, or null for any other link. A bare URL's source is
 * its text: a `<…>` autolink adds its brackets, and a link that a transform made
 * from escaped text has no position. */
function bareUrl(link: MdastNode): { text: string; scheme: string } | null {
  const [child, ...rest] = link.children ?? [];
  const start = link.position?.start.offset;
  const end = link.position?.end.offset;
  if (!child || rest.length || child.type !== 'text' || child.value === undefined || start === undefined || end === undefined) return null;
  const text = child.value;
  if (end - start !== text.length) return null;
  if (link.url === text) return { text, scheme: '' };
  if (link.url === `http://${text}`) return { text, scheme: 'http://' };
  return null;
}

function count(text: string, char: string) {
  return text.split(char).length - 1;
}

/** Whether an odd number of backslashes comes before `index`: the text of a bare URL
 * is its source, so "\[^a]" is an escaped bracket and not a reference. */
function escaped(text: string, index: number) {
  let backslashes = 0;
  while (text[index - backslashes - 1] === '\\') backslashes++;
  return backslashes % 2 === 1;
}

/** `url` without the end that GFM leaves out of a bare URL: punctuation, and a ")"
 * without its "(". */
function withoutTrail(url: string) {
  let end = url.length;
  while (end > 0) {
    const char = url[end - 1]!;
    const kept = url.slice(0, end);
    if (TRAILING_PUNCTUATION.has(char) || (char === ')' && count(kept, ')') > count(kept, '('))) end--;
    else break;
  }
  return end;
}

/** The nodes that replace a bare-URL link that holds references, or null. A "]" that
 * closes the last reference is taken from the start of `next`. */
function split(link: MdastNode, next: MdastNode | undefined, defined: Set<string>): MdastNode[] | null {
  const bare = bareUrl(link);
  if (!bare) return null;
  const { text, scheme } = bare;
  const closesInNext = next?.type === 'text' && !!next.value?.startsWith(']');
  const nodes: MdastNode[] = [];
  let last = 0;
  let tookBracket = false;
  for (const match of text.matchAll(CALL)) {
    const label = match[1]!;
    const closed = match[2] === ']';
    if ((!closed && !closesInNext) || escaped(text, match.index) || !defined.has(identifierOf(label))) continue;
    const before = text.slice(last, match.index);
    if (!nodes.length) {
      // The part before the first reference stays a link, as GFM would have linked it.
      const end = withoutTrail(before);
      if (end > 0) nodes.push({ type: 'link', url: scheme + before.slice(0, end), title: null, children: [{ type: 'text', value: before.slice(0, end) }] });
      if (end < before.length) nodes.push({ type: 'text', value: before.slice(end) });
    } else if (before) {
      nodes.push({ type: 'text', value: before });
    }
    nodes.push({ type: 'footnoteReference', identifier: identifierOf(label), label });
    last = match.index + match[0].length;
    tookBracket = !closed;
  }
  if (!nodes.length) return null;
  if (last < text.length) nodes.push({ type: 'text', value: text.slice(last) });
  if (tookBracket && next) next.value = next.value!.slice(1);
  return nodes;
}

function repair(parent: MdastNode, defined: Set<string>) {
  const children = parent.children;
  if (!children) return;
  for (let index = 0; index < children.length; index++) {
    const node = children[index]!;
    const replacement = node.type === 'link' ? split(node, children[index + 1], defined) : null;
    if (!replacement) { repair(node, defined); continue; }
    children.splice(index, 1, ...replacement);
    index += replacement.length - 1;
    const next = children[index + 1];
    if (next?.type === 'text' && next.value === '') children.splice(index + 1, 1);
  }
}

/** A remark plugin, after remark-gfm: footnote references that a bare URL took
 * become references again. */
export function urlFootnoteReferences() {
  return (tree: MdastNode) => {
    const defined = definedFootnotes(tree);
    if (defined.size) repair(tree, defined);
  };
}
