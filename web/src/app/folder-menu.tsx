import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { MoreHorizontal } from 'lucide-react';

export type RowMenuItem = { label: string; icon: ReactNode; onSelect: () => void; disabled?: boolean; hint?: string };

// A small menu button (WAI-ARIA menu pattern): Enter, Space, or ArrowDown opens it,
// arrows, Home, and End move, Escape and Tab close it. Disabled items stay
// focusable, so their hint is read.
export function RowMenu({ label, items }: { label: string; items: RowMenuItem[] }) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    menu.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    const close = (event: PointerEvent) => {
      if (event.target instanceof Node && !menu.current?.contains(event.target) && !trigger.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener('pointerdown', close);
    return () => document.removeEventListener('pointerdown', close);
  }, [open]);
  function closeToTrigger() { setOpen(false); trigger.current?.focus(); }
  function onMenuKey(event: KeyboardEvent<HTMLDivElement>) {
    const entries = Array.from(menu.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
    const index = entries.findIndex(entry => entry === document.activeElement);
    const go = (next: number) => { event.preventDefault(); entries[(next + entries.length) % entries.length]?.focus(); };
    if (event.key === 'ArrowDown') go(index + 1);
    else if (event.key === 'ArrowUp') go(index - 1);
    else if (event.key === 'Home') go(0);
    else if (event.key === 'End') go(entries.length - 1);
    else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeToTrigger(); }
    else if (event.key === 'Tab') setOpen(false);
  }
  return <div className="row-menu">
    <button ref={trigger} type="button" className="row-menu-trigger" aria-label={label} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? id : undefined}
      onClick={() => setOpen(value => !value)} onKeyDown={event => { if (event.key === 'ArrowDown' && !open) { event.preventDefault(); setOpen(true); } }}>
      <MoreHorizontal size={16} />
    </button>
    {open && <div ref={menu} id={id} role="menu" aria-label={label} className="row-menu-list" onKeyDown={onMenuKey}>
      {items.map((item, index) => <button key={item.label} type="button" role="menuitem" tabIndex={-1} aria-disabled={item.disabled || undefined}
        aria-labelledby={`${id}-label-${index}`} aria-describedby={item.hint ? `${id}-hint-${index}` : undefined}
        onClick={() => { if (item.disabled) return; closeToTrigger(); item.onSelect(); }}>
        {item.icon}<span><span id={`${id}-label-${index}`}>{item.label}</span>{item.hint && <small id={`${id}-hint-${index}`}>{item.hint}</small>}</span>
      </button>)}
    </div>}
  </div>;
}
