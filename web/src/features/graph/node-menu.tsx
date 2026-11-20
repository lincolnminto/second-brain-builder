import { useEffect, useLayoutEffect, useRef, type KeyboardEvent } from 'react';
import { Crosshair, ExternalLink, FileText, Link2, Network, Pin, PinOff, Share2, type LucideIcon } from 'lucide-react';
import type { MenuAction, MenuItem } from './menu';
import { strings } from './strings';
import type { Point } from './sync';

const icons: Record<MenuAction, LucideIcon> = {
  open: FileText, focus: Crosshair, 'neighbors-1': Share2, 'neighbors-2': Network,
  pin: Pin, unpin: PinOff, 'copy-link': Link2, 'open-source': ExternalLink,
};
const MARGIN = 8;

type Props = {
  title: string;
  items: MenuItem[];
  /** Where the menu opens, relative to its positioned parent. */
  point: Point;
  onAction: (action: MenuAction) => void;
  onClose: () => void;
};

/** The actions of one page, at the node or at its "Actions" button. Arrow
 * keys move between items; Escape, Tab, or a click outside closes it. */
export function NodeMenu({ title, items, point, onAction, onClose }: Props) {
  const menu = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  useEffect(() => { close.current = onClose; });

  useLayoutEffect(() => {
    const element = menu.current!;
    const parent = element.offsetParent;
    const width = parent?.clientWidth ?? window.innerWidth;
    const height = parent?.clientHeight ?? window.innerHeight;
    element.style.left = `${Math.max(MARGIN, Math.min(point.x, width - element.offsetWidth - MARGIN))}px`;
    element.style.top = `${Math.max(MARGIN, Math.min(point.y, height - element.offsetHeight - MARGIN))}px`;
    element.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
  }, [point]);

  useEffect(() => {
    const outside = (event: PointerEvent) => {
      if (!menu.current?.contains(event.target as Node)) close.current();
    };
    document.addEventListener('pointerdown', outside, true);
    return () => document.removeEventListener('pointerdown', outside, true);
  }, []);

  function keyboard(event: KeyboardEvent<HTMLDivElement>) {
    const entries = [...menu.current!.querySelectorAll<HTMLElement>('[role="menuitem"]')];
    const index = entries.indexOf(document.activeElement as HTMLElement);
    const move = (next: number) => entries[(next + entries.length) % entries.length]?.focus();
    switch (event.key) {
      case 'ArrowDown': move(index + 1); break;
      case 'ArrowUp': move(index - 1); break;
      case 'Home': move(0); break;
      case 'End': move(entries.length - 1); break;
      case 'Escape': case 'Tab': onClose(); break;
      default: return;
    }
    event.preventDefault();
    event.stopPropagation();
  }

  return <div ref={menu} className="graph-menu" role="menu" aria-label={strings.menu(title)} onKeyDown={keyboard}
    onContextMenu={event => event.preventDefault()} style={{ left: point.x, top: point.y }}>
    {items.map(item => {
      const Icon = icons[item.action];
      return <button key={item.action} type="button" role="menuitem" tabIndex={-1} onClick={() => onAction(item.action)}>
        <Icon size={15} aria-hidden="true" />{item.label}
      </button>;
    })}
  </div>;
}
