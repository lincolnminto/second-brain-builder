import { opensSourceView, pageUrl, type GraphNode } from './data';
import { strings } from './strings';

export type MenuAction = 'open' | 'focus' | 'neighbors-1' | 'neighbors-2' | 'pin' | 'unpin' | 'copy-link' | 'open-source';
export type MenuItem = { action: MenuAction; label: string };
type MenuNode = Pick<GraphNode, 'path' | 'type'>;

/** The actions of one node, in menu order. A Canonical Document opens in its
 * Source view, so its first action is "Open Source" instead of "Open page". */
export function menuItems(brainId: string, node: MenuNode, pinned: boolean): MenuItem[] {
  return [
    opensSourceView(brainId, node) ? { action: 'open-source', label: strings.menuOpenSource } : { action: 'open', label: strings.menuOpen },
    { action: 'focus', label: strings.menuFocus },
    { action: 'neighbors-1', label: strings.menuNeighbors1 },
    { action: 'neighbors-2', label: strings.menuNeighbors2 },
    pinned ? { action: 'unpin', label: strings.menuUnpin } : { action: 'pin', label: strings.menuPin },
    { action: 'copy-link', label: strings.menuCopy },
  ];
}

export type MenuHandlers = {
  brainId: string;
  /** The page origin, for absolute links. */
  origin: string;
  navigate: (to: string) => void;
  focus: (path: string) => void;
  showNeighbors: (path: string, depth: 1 | 2) => void;
  setPinned: (path: string, pinned: boolean) => void;
  copy: (text: string) => Promise<void>;
};
export type MenuOutcome = 'done' | 'copied' | 'copy-failed';

export async function runMenuAction(action: MenuAction, node: MenuNode, handlers: MenuHandlers): Promise<MenuOutcome> {
  switch (action) {
    case 'open': case 'open-source': handlers.navigate(pageUrl(handlers.brainId, node)); break;
    case 'focus': handlers.focus(node.path); break;
    case 'neighbors-1': handlers.showNeighbors(node.path, 1); break;
    case 'neighbors-2': handlers.showNeighbors(node.path, 2); break;
    case 'pin': handlers.setPinned(node.path, true); break;
    case 'unpin': handlers.setPinned(node.path, false); break;
    case 'copy-link':
      try {
        await handlers.copy(`${handlers.origin}${pageUrl(handlers.brainId, node)}`);
        return 'copied';
      } catch {
        return 'copy-failed';
      }
  }
  return 'done';
}
