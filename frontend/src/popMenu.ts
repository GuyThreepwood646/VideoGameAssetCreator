// Shared rules for small pop-up menus that hang off a button (the viewer's Light menu, the top bar's View menu).

/** At this window width and below, a pop-up is a full-width sheet at the bottom of the window (see styles.css). */
export const SHEET_MAX_WIDTH = 700

type Box = { left: number; top: number; right: number; bottom: number; width: number; height: number }
export type Placement = { top: number; left: number; maxHeight: number } | null

/**
 * Where a pop-up sits when it is fixed to the window instead of to its button's box: under the button when it fits,
 * otherwise above it, and never past the window's edges. A fixed pop-up is not cut off by a parent's overflow:hidden.
 * Returns null when the window is narrow enough for the bottom sheet, which the stylesheet places.
 */
export function popPlacement(button: Box, pop: { width: number; height: number }, view: { width: number; height: number }, gap = 4, margin = 12): Placement {
  if (view.width <= SHEET_MAX_WIDTH) return null
  const below = view.height - button.bottom - gap - margin
  const above = button.top - gap - margin
  const useBelow = pop.height <= below || below >= above
  const room = Math.max(120, useBelow ? below : above)
  const height = Math.min(pop.height, room)
  const top = useBelow ? button.bottom + gap : button.top - gap - height
  const left = Math.max(margin, Math.min(button.left, view.width - margin - pop.width))
  return { top, left, maxHeight: room }
}

/** True when a click landed outside the menu's wrapper, so it should close. Anything inside (button or pop-up) never does. */
export function isOutsideClick(wrap: { contains(node: unknown): boolean } | null, target: unknown): boolean {
  return !!wrap && !wrap.contains(target)
}
