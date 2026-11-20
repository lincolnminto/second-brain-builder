/**
 * A remark plugin for one segment of a long page: a footnote definition that no
 * reference in the same Markdown uses stays in the text as a paragraph that starts
 * with its label ("[^1]: The note."). Without it, the definition is dropped.
 *
 * The viewer parses each segment of a long page alone, so a footnote reference and
 * its definition can be in different segments. Then the reference shows as its
 * source text (`[^1]`) and the definition shows where it is, with its label.
 *
 * The mdast types are not a direct dependency, so this file declares the small
 * part of them that it uses.
 */

interface MdastNode {
  type: string;
  children?: MdastNode[];
  identifier?: string;
  label?: string;
  value?: string;
}

export function remarkKeepOrphanFootnotes() {
  return (tree: MdastNode) => {
    const referenced = new Set<string>();
    collectReferences(tree, referenced);
    keepOrphans(tree, referenced);
  };
}

function collectReferences(node: MdastNode, referenced: Set<string>): void {
  if (node.type === 'footnoteReference' && node.identifier !== undefined) referenced.add(node.identifier);
  for (const child of node.children ?? []) collectReferences(child, referenced);
}

function keepOrphans(parent: MdastNode, referenced: ReadonlySet<string>): void {
  if (!parent.children) return;
  const children: MdastNode[] = [];
  for (const child of parent.children) {
    keepOrphans(child, referenced);
    if (child.type !== 'footnoteDefinition' || child.identifier === undefined || referenced.has(child.identifier)) {
      children.push(child);
      continue;
    }
    // The label is generated text, with no source position: highlights skip it.
    const label: MdastNode = { type: 'text', value: `[^${child.label ?? child.identifier}]: ` };
    const [first, ...rest] = child.children ?? [];
    if (first?.type === 'paragraph') children.push({ ...first, children: [label, ...(first.children ?? [])] }, ...rest);
    else children.push({ type: 'paragraph', children: [label] }, ...(child.children ?? []));
  }
  parent.children = children;
}
