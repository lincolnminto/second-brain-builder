/** The CSS max-width of a chip tooltip (see `.knowledge-tooltip`). */
export const TOOLTIP_MAX_WIDTH = 260;
const TOOLTIP_MARGIN = 8;

/** Where a tooltip starts, as an offset from the left edge of its anchor,
 * and how wide it may be, so it stays inside `bounds` (a frame that clips
 * its overflow). `width` is the tooltip's natural width. */
export function tooltipPlacement(anchorLeft: number, width: number, bounds: { left: number; right: number }, margin = TOOLTIP_MARGIN) {
  const maxWidth = Math.max(0, Math.min(TOOLTIP_MAX_WIDTH, bounds.right - bounds.left - 2 * margin));
  const fitted = Math.min(width, maxWidth);
  const left = Math.max(bounds.left + margin, Math.min(anchorLeft, bounds.right - margin - fitted));
  return { offset: left - anchorLeft, maxWidth };
}

/** Keep a chip's tooltip inside the graph frame. Call it before the tooltip shows. */
export function placeTooltip(chip: HTMLElement) {
  const tooltip = chip.querySelector<HTMLElement>('[role="tooltip"]');
  const frame = chip.closest<HTMLElement>('.knowledge-frame');
  if (!tooltip || !frame) return;
  const bounds = frame.getBoundingClientRect();
  if (bounds.width <= 0) return;
  // Measure at the natural width: the tooltip is laid out while hidden.
  tooltip.style.left = '';
  tooltip.style.maxWidth = '';
  const { offset, maxWidth } = tooltipPlacement(chip.getBoundingClientRect().left, tooltip.offsetWidth, bounds);
  tooltip.style.left = `${offset}px`;
  tooltip.style.maxWidth = `${maxWidth}px`;
}
