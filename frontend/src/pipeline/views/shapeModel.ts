// The Shape tab's settings and geometry helpers (WP-26). Pure functions: the tab keeps the state, the backend does the
// cut. Ranges match backend/vgac/separate.py (GAP_RANGE, START_RANGE) and main.py ShapeIn.
import type { ShapeArm, ShapeBody, ShapeLog } from '../../api'

export type ShapeSide = 'L' | 'R' | 'both'
export type ShapeSettings = { side: ShapeSide; gapMm: number; startPercent: number }

export const GAP_MM = { min: 2, max: 20, step: 1 }
export const START_PERCENT = { min: 15, max: 60, step: 1 }
export const DEFAULT_SHAPE: ShapeSettings = { side: 'both', gapMm: 6, startPercent: 30 }

export const SIDE_CHOICES: { side: ShapeSide; label: string }[] = [
  { side: 'L', label: 'Left arm' },
  { side: 'R', label: 'Right arm' },
  { side: 'both', label: 'Both arms' },
]

export function clampShape(s: ShapeSettings): ShapeSettings {
  const side: ShapeSide = s.side === 'L' || s.side === 'R' ? s.side : 'both'
  const gapMm = Math.min(GAP_MM.max, Math.max(GAP_MM.min, Math.round(s.gapMm)))
  const startPercent = Math.min(START_PERCENT.max, Math.max(START_PERCENT.min, Math.round(s.startPercent)))
  return { side, gapMm, startPercent }
}

// The request body: parameters only, in the units the server takes (metres and a share of the upper arm).
export function shapeBody(s: ShapeSettings): ShapeBody {
  const c = clampShape(s)
  return { operation: 'separate', side: c.side, gap: c.gapMm / 1000, start: c.startPercent / 100 }
}

export function armWords(side: ShapeSide): string {
  return SIDE_CHOICES.find((c) => c.side === side)?.label.toLowerCase() ?? 'arms'
}

export function gapWords(mm: number): string {
  return `${mm} mm`
}

export function startWords(percent: number): string {
  return `${percent} percent of the way down the upper arm, from the shoulder`
}

// The arm nearest a point on the model: the side whose shoulder-to-elbow segment is closest. Null with no arms.
export function nearestArm(point: { x: number; y: number; z: number }, arms: Partial<Record<'L' | 'R', ShapeArm>>): 'L' | 'R' | null {
  let best: 'L' | 'R' | null = null
  let bestD = Infinity
  for (const side of ['L', 'R'] as const) {
    const arm = arms[side]
    if (!arm) continue
    const d = distanceToSegment(point, arm.shoulder, arm.elbow)
    if (d < bestD) {
      bestD = d
      best = side
    }
  }
  return best
}

export function distanceToSegment(p: { x: number; y: number; z: number }, a: number[], b: number[]): number {
  const ab = [b[0] - a[0], b[1] - a[1], b[2] - a[2]]
  const ap = [p.x - a[0], p.y - a[1], p.z - a[2]]
  const len2 = ab[0] * ab[0] + ab[1] * ab[1] + ab[2] * ab[2] || 1e-12
  const t = Math.min(1, Math.max(0, (ap[0] * ab[0] + ap[1] * ab[1] + ap[2] * ab[2]) / len2))
  const q = [a[0] + t * ab[0] - p.x, a[1] + t * ab[1] - p.y, a[2] + t * ab[2] - p.z]
  return Math.hypot(q[0], q[1], q[2])
}

// The cut-start marker for one arm: a ring in the plane the cut starts in (normal = the upper arm's axis, centred on the
// cut start). It is wider than the arm's own radius and thick enough to read at the tab's size on a whole character.
export function cutRing(arm: ShapeArm): { center: [number, number, number]; normal: [number, number, number]; inner: number; outer: number } {
  const outer = Math.max(arm.radius_m * 1.6, 0.11)
  return {
    center: [arm.cut_start[0], arm.cut_start[1], arm.cut_start[2]],
    normal: [arm.axis[0], arm.axis[1], arm.axis[2]],
    inner: outer * 0.7,
    outer,
  }
}

// Where the camera looks when the tab opens: the chosen arm's cut start, or the midpoint of both when both arms are
// chosen, with a radius that keeps the marks in view. Model space (the caller converts to the world).
export function focusCentre(arms: Partial<Record<'L' | 'R', ShapeArm>>, side: ShapeSide): { point: [number, number, number]; radius: number } | null {
  const sides = side === 'both' ? (['L', 'R'] as const) : ([side] as const)
  const starts = sides.map((s) => arms[s]?.cut_start).filter((p): p is number[] => !!p)
  if (!starts.length) return null
  const mid = [0, 1, 2].map((k) => starts.reduce((a, p) => a + p[k], 0) / starts.length) as [number, number, number]
  const spread = Math.max(...starts.map((p) => Math.hypot(p[0] - mid[0], p[1] - mid[1], p[2] - mid[2])))
  return { point: mid, radius: spread + 0.2 }
}

// The engine's model check in words (its numbers stay in metrics.shape_log.engine). The arm stays part of the one model
// (it moves with the skeleton); what is new is a closed surface on the arm and on the body where they were joined.
export function shapeCheckLine(e: ShapeLog['engine']): string {
  const whole = e.pieces === 1 && e.open_edges === 0 && e.bad_edges === 0 && e.new_crossings === 0
  const closed = e.caps_placed > 0 ? ` (${e.caps_placed} new surfaces in all)` : ''
  if (whole) {
    return `The model is still one closed piece with no holes: the arm and the body now each have their own surface where they were joined${closed}.`
  }
  return 'The model check found holes, overlaps or loose pieces after the cut. Look at the model closely before keeping it.'
}
