type MarkdownNode = { type: string; value?: string; children?: MarkdownNode[]; data?: { hProperties?: Record<string, unknown> } };
function text(node: MarkdownNode): string { return node.value ?? node.children?.map(text).join('') ?? ''; }

// Stable fragments make /wiki/...md#heading links useful without accepting HTML.
export function headingIds() {
  return (tree: MarkdownNode) => {
    const used = new Set<string>();
    function visit(node: MarkdownNode) {
      if (node.type === 'heading') {
        const base = text(node).toLowerCase().replace(/[^\p{L}\p{N}\s_-]/gu, '').replace(/\s/g, '-');
        let id = base;
        for (let index = 1; used.has(id); index++) id = `${base}-${index}`;
        used.add(id);
        node.data = { ...node.data, hProperties: { ...node.data?.hProperties, id } };
      }
      node.children?.forEach(visit);
    }
    visit(tree);
  };
}
